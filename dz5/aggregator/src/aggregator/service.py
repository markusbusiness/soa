import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from aggregator.clickhouse_jobs import ClickHouseAggregates
from aggregator.config import Settings
from aggregator.pg_store import PostgresStore
from aggregator.retry import with_retry

log = logging.getLogger("aggregator.cycle")

INGEST_VISIBILITY_OVERLAP = timedelta(seconds=60)
COHORT_WINDOW_DAYS = 7


@dataclass
class CycleResult:
    trigger: str
    dates: list[date]
    events_processed: int = 0
    rows_written: int = 0
    failed_dates: list[date] = field(default_factory=list)
    duration_seconds: float = 0.0


class AggregationService:
    def __init__(self, settings: Settings, clickhouse: ClickHouseAggregates, store: PostgresStore):
        self._settings = settings
        self._ch = clickhouse
        self._store = store
        self._lock = asyncio.Lock()

    async def _retry(self, operation: str, func):
        return await with_retry(operation, func, self._settings.retry_attempts, self._settings.retry_base_seconds)

    async def scheduled_cycle(self) -> CycleResult:
        async with self._lock:
            watermark = await self._retry("postgres read watermark", self._store.watermark)
            since = watermark - INGEST_VISIBILITY_OVERLAP if watermark else None
            touched, new_watermark = await self._retry(
                "clickhouse touched dates", lambda: asyncio.to_thread(self._ch.touched_dates, since))
            today = datetime.now(timezone.utc).date()
            dates: list[date] = []
            if touched:
                start = touched[0] - timedelta(days=COHORT_WINDOW_DAYS)
                dates = await self._retry("clickhouse dates with events",
                                          lambda: asyncio.to_thread(self._ch.dates_with_events, start, today))
            result = await self._run(dates, "schedule")
            if new_watermark is not None and not result.failed_dates:
                await self._retry("postgres save watermark", lambda: self._store.save_watermark(new_watermark))
            return result

    async def recompute(self, metric_date: date) -> CycleResult:
        async with self._lock:
            return await self._run([metric_date], "manual")

    async def _run(self, dates: list[date], trigger: str) -> CycleResult:
        result = CycleResult(trigger=trigger, dates=dates)
        started = time.monotonic()
        log.info("aggregation cycle started trigger=%s dates=%d range=%s..%s", trigger, len(dates),
                 dates[0] if dates else "-", dates[-1] if dates else "-")
        for d in dates:
            try:
                events, rows = await self._process_date(d)
                result.events_processed += events
                result.rows_written += rows
            except Exception:
                log.exception("aggregation failed for %s, will retry next cycle", d)
                result.failed_dates.append(d)
        result.duration_seconds = time.monotonic() - started
        log.info("aggregation cycle finished trigger=%s dates=%d events_processed=%d rows_written=%d "
                 "failed_dates=%s duration=%.2fs", trigger, len(dates), result.events_processed,
                 result.rows_written, [str(d) for d in result.failed_dates] or "none", result.duration_seconds)
        return result

    async def _process_date(self, metric_date: date) -> tuple[int, int]:
        now = datetime.now(timezone.utc)
        computed_at = now.replace(microsecond=now.microsecond // 1000 * 1000)
        aggregates = await self._retry(
            f"clickhouse aggregate {metric_date}",
            lambda: asyncio.to_thread(self._ch.compute, metric_date, now.date(), computed_at,
                                      self._settings.top_movies_limit))
        written = await self._retry(f"postgres upsert {metric_date}", lambda: self._store.write(aggregates))
        log.info("date aggregated date=%s events=%d rows_written=%d changed=%s",
                 metric_date, aggregates.events_processed, written.rows_written, written.changed)
        return aggregates.events_processed, written.rows_written

    async def run_forever(self) -> None:
        while True:
            try:
                await self.scheduled_cycle()
            except Exception:
                log.exception("aggregation cycle aborted, will retry next cycle")
            await asyncio.sleep(self._settings.interval_seconds)
