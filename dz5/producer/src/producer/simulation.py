import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

ID_NAMESPACE = uuid.UUID("6f1d3c1e-5b7a-4f43-9d55-1c2b0c7e9a10")
DEVICES = ["MOBILE", "DESKTOP", "TV", "TABLET"]
SEARCH_WORDS = ["комедия", "драма", "фантастика", "сериал", "мультфильм", "детектив", "триллер", "новинки",
                "2026", "космос", "любовь", "война", "семейный", "ужасы", "документальный"]
ACTIVITY_HORIZON_DAYS = 45
RETURN_DECAY = 0.85
SESSION_START_SECONDS = (7 * 3600, 22 * 3600)


@dataclass(frozen=True)
class PlannedEvent:
    offset_seconds: float
    event_type: str
    progress_seconds: int | None


def plan_session(rng: random.Random, movie_duration: int) -> list[PlannedEvent]:
    """Сценарий одной сессии: [поиск] → старт → (пауза → продолжение)* → финиш [→ лайк] или брошенный просмотр."""
    events: list[PlannedEvent] = []
    t = 0.0
    if rng.random() < 0.35:
        events.append(PlannedEvent(t, "SEARCHED", None))
        t += rng.uniform(5, 60)
    events.append(PlannedEvent(t, "VIEW_STARTED", 0))

    finished = rng.random() < 0.7
    watched = movie_duration if finished else int(movie_duration * rng.uniform(0.05, 0.8))
    pause_points = sorted(rng.sample(range(1, watched), k=min(rng.randint(0, 3), watched - 1)))
    progress = 0
    for point in pause_points:
        t += point - progress
        progress = point
        events.append(PlannedEvent(t, "VIEW_PAUSED", point))
        t += rng.uniform(10, 600)
        events.append(PlannedEvent(t, "VIEW_RESUMED", point))
    t += watched - progress

    if finished:
        events.append(PlannedEvent(t, "VIEW_FINISHED", movie_duration))
        if rng.random() < 0.35:
            events.append(PlannedEvent(t + rng.uniform(1, 30), "LIKED", None))
    else:
        events.append(PlannedEvent(t, "VIEW_PAUSED", watched))
    return events


@dataclass
class Catalog:
    movie_ids: list[str]
    durations: dict[str, int]
    weights: list[float]

    @classmethod
    def build(cls, size: int, seed: str) -> "Catalog":
        rng = random.Random(f"{seed}:catalog")
        movie_ids = [f"movie-{i:04d}" for i in range(1, size + 1)]
        return cls(
            movie_ids=movie_ids,
            durations={m: rng.randint(20, 150) * 60 for m in movie_ids},
            weights=[1 / (rank ** 1.1) for rank in range(1, size + 1)],
        )

    def pick(self, rng: random.Random) -> str:
        return rng.choices(self.movie_ids, weights=self.weights)[0]


def user_device(user_id: str, rng: random.Random) -> str:
    preferred = DEVICES[uuid.uuid5(ID_NAMESPACE, user_id).int % len(DEVICES)]
    return preferred if rng.random() < 0.8 else rng.choice(DEVICES)


def search_query(movie_id: str) -> str:
    rng = random.Random(movie_id)
    return " ".join(rng.sample(SEARCH_WORDS, k=rng.randint(1, 3)))


class Population:
    """Пользователи приходят начиная с launch; вероятность вернуться в день N жизни = stickiness · 0.85^(N−1)."""

    def __init__(self, seed: str, launch: date, new_users_per_day: int):
        self.seed = seed
        self.launch = launch
        self.new_users_per_day = new_users_per_day
        self._stickiness: dict[str, float] = {}

    def arrivals(self, day: date) -> list[str]:
        if day < self.launch:
            return []
        rng = random.Random(f"{self.seed}:arrivals:{day.isoformat()}")
        count = round(self.new_users_per_day * rng.uniform(0.7, 1.3))
        return [f"user-{day:%Y%m%d}-{i:04d}" for i in range(count)]

    def stickiness(self, user_id: str) -> float:
        if user_id not in self._stickiness:
            self._stickiness[user_id] = random.Random(f"{self.seed}:{user_id}").betavariate(2, 3)
        return self._stickiness[user_id]


def day_records(population: Population, catalog: Catalog, day: date) -> list[dict]:
    """События сессий, начатых в day. Зависят только от seed, launch и day, поэтому event_id стабильны."""
    records: list[dict] = []
    midnight = datetime.combine(day, time(), tzinfo=timezone.utc)
    for age in range(ACTIVITY_HORIZON_DAYS + 1):
        first_day = day - timedelta(days=age)
        for user_id in population.arrivals(first_day):
            rng = random.Random(f"{population.seed}:{user_id}:{day.isoformat()}")
            if age > 0 and rng.random() >= population.stickiness(user_id) * RETURN_DECAY ** (age - 1):
                continue
            for session_no in range(1 + (rng.random() < 0.3)):
                key = f"{population.seed}:{user_id}:{day.isoformat()}:{session_no}"
                movie_id = catalog.pick(rng)
                device = user_device(user_id, rng)
                session_id = str(uuid.uuid5(ID_NAMESPACE, key))
                start = midnight + timedelta(seconds=rng.uniform(*SESSION_START_SECONDS))
                for n, planned in enumerate(plan_session(rng, catalog.durations[movie_id])):
                    records.append({
                        "event_id": str(uuid.uuid5(ID_NAMESPACE, f"{key}:{n}")),
                        "user_id": user_id,
                        "movie_id": movie_id,
                        "event_type": planned.event_type,
                        "timestamp": start + timedelta(seconds=planned.offset_seconds),
                        "device_type": device,
                        "session_id": session_id,
                        "progress_seconds": planned.progress_seconds,
                        "search_query": search_query(movie_id) if planned.event_type == "SEARCHED" else None,
                    })
    records.sort(key=lambda r: r["timestamp"])
    return records


class EventSchedule:
    """Все события в порядке timestamp. Сессия длится меньше суток, поэтому событие дня D лежит в плане D или D−1."""

    def __init__(self, population: Population, catalog: Catalog):
        self._population = population
        self._catalog = catalog
        self._plans: dict[date, list[dict]] = {}

    def _plan(self, day: date) -> list[dict]:
        if day not in self._plans:
            self._plans[day] = day_records(self._population, self._catalog, day)
        return self._plans[day]

    def _days(self, first: date, last: date) -> list[date]:
        return [first + timedelta(days=i) for i in range((last - first).days + 1)]

    def between(self, after: datetime, until: datetime) -> list[dict]:
        """События с after < timestamp <= until."""
        first = after.date() - timedelta(days=1)
        for day in [d for d in self._plans if d < first]:
            del self._plans[day]
        due = [r for day in self._days(first, until.date()) for r in self._plan(day)
               if after < r["timestamp"] <= until]
        due.sort(key=lambda r: r["timestamp"])
        return due

    def next_after(self, after: datetime) -> datetime | None:
        days = self._days(after.date() - timedelta(days=1), after.date() + timedelta(days=1))
        return min((r["timestamp"] for day in days for r in self._plan(day) if r["timestamp"] > after), default=None)
