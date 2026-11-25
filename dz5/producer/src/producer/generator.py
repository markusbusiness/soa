import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from producer.config import GeneratorSettings
from producer.publisher import KafkaPublisher
from producer.simulation import Catalog, EventSchedule, Population

log = logging.getLogger("producer.generator")

BATCH_SIZE = 500
MAX_IDLE_SECONDS = 60


@dataclass
class GeneratorState:
    launch: date
    published_until: datetime


class StateStore:
    def __init__(self, state_dir: str):
        self._path = Path(state_dir) / "generator.json"

    def load(self) -> GeneratorState | None:
        if not self._path.exists():
            return None
        raw = json.loads(self._path.read_text())
        return GeneratorState(date.fromisoformat(raw["launch"]), datetime.fromisoformat(raw["published_until"]))

    def save(self, state: GeneratorState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"launch": state.launch.isoformat(),
                                   "published_until": state.published_until.isoformat()}))
        tmp.replace(self._path)


class EventGenerator:
    def __init__(self, settings: GeneratorSettings, publisher: KafkaPublisher):
        self.settings = settings
        self.publisher = publisher
        self.catalog = Catalog.build(settings.movies, settings.seed)
        self.store = StateStore(settings.state_dir)
        self.state: GeneratorState | None = None
        self.published = 0
        self.failed = 0
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._run(), name="event-generator")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _initial_state(self) -> GeneratorState:
        state = self.store.load()
        if state is None:
            launch = datetime.now(timezone.utc).date() - timedelta(days=self.settings.backfill_days)
            state = GeneratorState(launch, datetime.combine(launch, time(), tzinfo=timezone.utc))
            self.store.save(state)
        return state

    async def _run(self) -> None:
        self.state = await asyncio.to_thread(self._initial_state)
        population = Population(self.settings.seed, self.state.launch, self.settings.new_users_per_day)
        schedule = EventSchedule(population, self.catalog)
        log.info("generator started launch=%s published_until=%s new_users_per_day=%d",
                 self.state.launch, self.state.published_until.isoformat(), self.settings.new_users_per_day)
        failed_attempts = 0
        while True:
            try:
                now = datetime.now(timezone.utc)
                due = await asyncio.to_thread(schedule.between, self.state.published_until, now)
                failures = await self._publish(due)
                if failures:
                    failed_attempts += 1
                    delay = min(MAX_IDLE_SECONDS, 2 ** failed_attempts)
                    log.error("generator: %d of %d events failed, republishing since %s in %ds",
                              failures, len(due), self.state.published_until.isoformat(), delay)
                    await asyncio.sleep(delay)
                    continue
                failed_attempts = 0
                if len(due) > 1:
                    log.info("generator caught up events=%d until=%s", len(due), now.isoformat())
                self.state.published_until = now
                await asyncio.to_thread(self.store.save, self.state)
                next_at = await asyncio.to_thread(schedule.next_after, now)
                wait = (next_at - datetime.now(timezone.utc)).total_seconds() if next_at else MAX_IDLE_SECONDS
                await asyncio.sleep(min(MAX_IDLE_SECONDS, max(0.05, wait)))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("generator iteration failed, retrying")
                await asyncio.sleep(5)

    async def _publish(self, records: list[dict]) -> int:
        failures = 0
        for start in range(0, len(records), BATCH_SIZE):
            results = await asyncio.gather(*(self.publisher.publish(r) for r in records[start:start + BATCH_SIZE]),
                                           return_exceptions=True)
            batch_failures = sum(isinstance(r, Exception) for r in results)
            failures += batch_failures
            self.published += len(results) - batch_failures
            self.failed += batch_failures
        return failures
