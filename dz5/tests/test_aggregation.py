import uuid
from datetime import timedelta

import pytest

from conftest import at, event, free_day, publish_all, wait_for, wait_ingested

EXPECTED_METRICS = {
    "events_total": 12,
    "dau": 5,
    "wau": 5,
    "mau": 5,
    "sessions": 5,
    "views_started": 4,
    "views_finished": 3,
    "view_conversion": 0.75,
    "avg_view_seconds": 2000,
    "likes": 1,
    "searches": 2,
    "new_viewers": 4,
    "retention_d1": 0.5,
    "retention_d7": 0.5,
}
EXPECTED_COHORT = [(0, 4, 4, 1.0), (1, 4, 2, 0.5)] + [(k, 4, 0, 0.0) for k in range(2, 7)] + [(7, 4, 2, 0.5)]
EXPECTED_DEVICES = [("DESKTOP", 1, 1), ("MOBILE", 2, 1), ("TABLET", 1, 1), ("TV", 1, 1)]


def pg_metrics(postgres, day) -> dict:
    rows = postgres.execute("SELECT metric_name, value FROM daily_metrics WHERE metric_date = %s", (day,)).fetchall()
    return dict(rows)


def test_recompute_reports_processed_events(cohort_dataset):
    result = cohort_dataset["recompute"]
    assert result["events_processed"] == 12
    assert result["rows_written"] == len(EXPECTED_METRICS) + 3 + len(EXPECTED_DEVICES) + len(EXPECTED_COHORT)


def test_daily_metrics_in_postgres(cohort_dataset, postgres):
    assert pg_metrics(postgres, cohort_dataset["day"]) == pytest.approx(EXPECTED_METRICS)


def test_daily_metrics_materialized_in_clickhouse(cohort_dataset, clickhouse):
    rows = clickhouse.query("SELECT metric_name, value FROM agg_daily_metrics FINAL WHERE metric_date = {d:Date}",
                            parameters={"d": cohort_dataset["day"]}).result_rows
    assert dict(rows) == pytest.approx(EXPECTED_METRICS)


def test_top_movies(cohort_dataset, postgres):
    m1, m2, m3 = cohort_dataset["movies"]
    rows = postgres.execute(
        "SELECT rank, movie_id, views, unique_viewers, finished, likes FROM top_movies "
        "WHERE metric_date = %s ORDER BY rank", (cohort_dataset["day"],)).fetchall()
    assert rows == [(1, m1, 2, 2, 2, 0), (2, m2, 1, 1, 0, 0), (3, m3, 1, 1, 1, 1)]


def test_devices(cohort_dataset, postgres):
    rows = postgres.execute("SELECT device_type, users, views FROM device_daily WHERE metric_date = %s "
                            "ORDER BY device_type", (cohort_dataset["day"],)).fetchall()
    assert rows == EXPECTED_DEVICES


def test_retention_cohort(cohort_dataset, postgres, clickhouse):
    rows = postgres.execute(
        "SELECT day_offset, cohort_size, retained_users, retention_rate FROM retention_cohorts "
        "WHERE cohort_date = %s ORDER BY day_offset", (cohort_dataset["day"],)).fetchall()
    assert rows == EXPECTED_COHORT
    ch_rows = clickhouse.query(
        "SELECT day_offset, cohort_size, retained_users, retention_rate FROM agg_retention_cohort FINAL "
        "WHERE cohort_date = {d:Date} ORDER BY day_offset", parameters={"d": cohort_dataset["day"]}).result_rows
    assert [tuple(r) for r in ch_rows] == EXPECTED_COHORT


def test_recompute_is_idempotent(cohort_dataset, aggregator, postgres):
    day = cohort_dataset["day"]

    def snapshot():
        return {
            table: postgres.execute(f"SELECT count(*), max(computed_at), max(updated_at) FROM {table} "
                                    f"WHERE {column} = %s", (day,)).fetchone()
            for table, column in [("daily_metrics", "metric_date"), ("top_movies", "metric_date"),
                                  ("device_daily", "metric_date"), ("retention_cohorts", "cohort_date")]
        }

    before = snapshot()
    response = aggregator.post("/recompute", params={"date": day.isoformat()})
    assert response.status_code == 200, response.text
    after = snapshot()
    for table in before:
        count_before, computed_before, updated_before = before[table]
        count_after, computed_after, updated_after = after[table]
        assert count_after == count_before, table
        assert computed_after > computed_before, table
        assert updated_after == updated_before, table
    assert pg_metrics(postgres, day) == pytest.approx(EXPECTED_METRICS)


def test_metrics_endpoint(cohort_dataset, aggregator):
    body = aggregator.get("/metrics", params={"date": cohort_dataset["day"].isoformat()}).json()
    assert body["metrics"] == pytest.approx(EXPECTED_METRICS)
    assert [c["retention_rate"] for c in body["retention_cohort"]] == [c[3] for c in EXPECTED_COHORT]


def test_scheduler_picks_up_new_events_without_manual_trigger(producer, clickhouse, postgres):
    day = free_day(clickhouse)
    events = [event(f"user-sched-{i}-{day}", "movie-sched", "VIEW_STARTED", at(day, 10 + i), 0) for i in range(3)]
    publish_all(producer, events)
    wait_ingested(clickhouse, [e["event_id"] for e in events])
    value = wait_for(lambda: postgres.execute("SELECT value FROM daily_metrics WHERE metric_date = %s "
                                              "AND metric_name = 'dau'", (day,)).fetchone(),
                     timeout=60, message="scheduled aggregation of a new date")
    assert value[0] == 3


def test_future_date_rejected(aggregator):
    assert aggregator.post("/recompute", params={"date": "2999-01-01"}).status_code == 400


def test_late_first_view_moves_user_to_earlier_cohort(producer, clickhouse, postgres):
    day = free_day(clickhouse)
    later = day + timedelta(days=3)
    run = uuid.uuid4().hex[:8]
    mover, stayer = f"user-{run}-mover", f"user-{run}-stayer"
    first = [event(mover, "movie-x", "VIEW_STARTED", at(later, 10), 0),
             event(stayer, "movie-x", "VIEW_STARTED", at(later, 11), 0)]
    publish_all(producer, first)
    wait_ingested(clickhouse, [e["event_id"] for e in first])

    def new_viewers(d):
        row = postgres.execute("SELECT value FROM daily_metrics WHERE metric_date = %s AND metric_name = 'new_viewers'",
                               (d,)).fetchone()
        return row[0] if row else None

    wait_for(lambda: new_viewers(later) == 2, timeout=60, message="cohort D+3 of two users")

    late = [event(mover, "movie-x", "VIEW_STARTED", at(day, 10), 0)]
    publish_all(producer, late)
    wait_ingested(clickhouse, [e["event_id"] for e in late])
    wait_for(lambda: new_viewers(day) == 1 and new_viewers(later) == 1, timeout=60,
             message="late first view moves user to cohort D and out of cohort D+3")
    cohort = postgres.execute("SELECT day_offset, retained_users FROM retention_cohorts WHERE cohort_date = %s "
                              "AND day_offset IN (0, 3) ORDER BY day_offset", (day,)).fetchall()
    assert cohort == [(0, 1), (3, 1)]
