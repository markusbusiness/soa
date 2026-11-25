from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

import pytest

from producer.simulation import Catalog, EventSchedule, Population, day_records

LAUNCH = date(2026, 3, 1)
CATALOG = Catalog.build(60, "test")


def population() -> Population:
    return Population("test", LAUNCH, 80)


def midnight(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=timezone.utc)


def dau(records: list[dict], day: date) -> int:
    return len({r["user_id"] for r in records if r["timestamp"].date() == day})


@pytest.fixture(scope="module")
def month() -> list[dict]:
    schedule = EventSchedule(population(), CATALOG)
    return schedule.between(midnight(LAUNCH), midnight(LAUNCH + timedelta(days=30)))


def test_plan_is_deterministic():
    day = LAUNCH + timedelta(days=10)
    assert day_records(population(), CATALOG, day) == day_records(population(), CATALOG, day)


def test_event_time_matches_session_progress(month):
    sessions = defaultdict(list)
    for r in month:
        sessions[r["session_id"]].append(r)
    assert len(sessions) > 1000
    for events in sessions.values():
        started = next(r["timestamp"] for r in events if r["event_type"] == "VIEW_STARTED")
        for r in events:
            if r["progress_seconds"] is not None:
                assert (r["timestamp"] - started).total_seconds() >= r["progress_seconds"]


def test_schedule_never_returns_future_events_and_splits_without_gaps():
    schedule = EventSchedule(population(), CATALOG)
    a = midnight(LAUNCH + timedelta(days=5)) + timedelta(hours=20)
    b = a + timedelta(hours=5)
    c = b + timedelta(hours=7)
    whole = schedule.between(a, c)
    assert all(a < r["timestamp"] <= c for r in whole)
    assert [r["event_id"] for r in schedule.between(a, b) + schedule.between(b, c)] == [r["event_id"] for r in whole]
    session_start = {}
    for r in whole:
        session_start.setdefault(r["session_id"], r["timestamp"])
    next_midnight = midnight(a.date() + timedelta(days=1))
    assert any(r["timestamp"] > next_midnight and session_start[r["session_id"]] < next_midnight for r in whole)


def test_users_arrive_steadily_and_return_with_decay(month):
    first_seen = {}
    for r in month:
        first_seen.setdefault(r["user_id"], r["timestamp"].date())
    new_per_day = defaultdict(int)
    for day in first_seen.values():
        new_per_day[day] += 1
    assert all(55 <= new_per_day[LAUNCH + timedelta(days=i)] <= 105 for i in range(29))
    assert all(r["user_id"].startswith("user-") and r["timestamp"].date() >= LAUNCH for r in month)

    late = [dau(month, LAUNCH + timedelta(days=i)) for i in range(20, 29)]
    assert max(late) < 1.5 * min(late)
    assert 150 < sum(late) / len(late) < 450


def test_next_after_points_to_the_next_event():
    schedule = EventSchedule(population(), CATALOG)
    t = midnight(LAUNCH + timedelta(days=3)) + timedelta(hours=12)
    following = schedule.between(t, t + timedelta(hours=6))
    assert schedule.next_after(t) == following[0]["timestamp"]
