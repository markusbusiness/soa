import os
import uuid
from datetime import datetime, timezone

import httpx
from confluent_kafka import Producer

from conftest import event, serialize_with_version, wait_ingested

COLUMNS = "event_id, user_id, movie_id, event_type, timestamp, device_type, session_id, progress_seconds, search_query"


def stored(clickhouse, event_id: str) -> list[tuple]:
    return clickhouse.query(f"SELECT {COLUMNS}, kafka_partition FROM movie_events FINAL WHERE event_id = {{id:UUID}}",
                            parameters={"id": event_id}).result_rows


def test_event_reaches_clickhouse_with_all_fields(producer, clickhouse, run_id):
    ts = datetime(2026, 3, 1, 18, 30, 15, 123000, tzinfo=timezone.utc)
    body = event(f"user-{run_id}", f"movie-{run_id}", "VIEW_PAUSED", ts, progress=1234,
                 session_id=f"session-{run_id}", device="TV")
    response = producer.post("/events", json=body)
    assert response.status_code == 202, response.text

    wait_ingested(clickhouse, [body["event_id"]])
    rows = stored(clickhouse, body["event_id"])
    assert len(rows) == 1
    assert rows[0] == (uuid.UUID(body["event_id"]), f"user-{run_id}", f"movie-{run_id}", "VIEW_PAUSED", ts, "TV",
                       f"session-{run_id}", 1234, None, response.json()["partition"])


def test_search_query_stored(producer, clickhouse, run_id):
    body = event(f"user-{run_id}", f"movie-{run_id}", "SEARCHED", datetime.now(timezone.utc))
    body["search_query"] = "детектив 2026"
    assert producer.post("/events", json=body).status_code == 202
    wait_ingested(clickhouse, [body["event_id"]])
    assert stored(clickhouse, body["event_id"])[0][8] == "детектив 2026"


def test_message_written_with_schema_v1_is_ingested(clickhouse, run_id):
    ts = datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc)
    record = {"event_id": str(uuid.uuid4()), "user_id": f"user-{run_id}", "movie_id": f"movie-{run_id}",
              "event_type": "VIEW_STARTED", "timestamp": ts, "device_type": "MOBILE",
              "session_id": f"session-{run_id}", "progress_seconds": 0}
    value = serialize_with_version(1, record)
    v1_id = httpx.get(f"{os.environ['SCHEMA_REGISTRY_URL']}/subjects/movie-events-value/versions/1").json()["id"]
    assert int.from_bytes(value[1:5], "big") == v1_id
    producer = Producer({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"], "acks": "all"})
    producer.produce("movie-events", key=record["user_id"].encode(), value=value)
    assert producer.flush(15) == 0
    wait_ingested(clickhouse, [record["event_id"]])
    row = stored(clickhouse, record["event_id"])[0]
    assert row[3:9] == ("VIEW_STARTED", ts, "MOBILE", f"session-{run_id}", 0, None)


def test_null_progress_is_stored_as_null(producer, clickhouse, run_id):
    body = event(f"user-{run_id}", f"movie-{run_id}", "LIKED", datetime.now(timezone.utc))
    assert producer.post("/events", json=body).status_code == 202
    wait_ingested(clickhouse, [body["event_id"]])
    assert stored(clickhouse, body["event_id"])[0][7] is None


def test_redelivered_event_is_deduplicated(producer, clickhouse, run_id):
    body = event(f"user-{run_id}", f"movie-{run_id}", "VIEW_STARTED", datetime.now(timezone.utc), progress=0)
    for _ in range(3):
        assert producer.post("/events", json=body).status_code == 202
    wait_ingested(clickhouse, [body["event_id"]])
    assert len(stored(clickhouse, body["event_id"])) == 1


def test_session_order_preserved(producer, clickhouse, run_id):
    user = f"user-{run_id}"
    ts = datetime(2026, 3, 2, 20, 0, tzinfo=timezone.utc)
    steps = [("VIEW_STARTED", 0), ("VIEW_PAUSED", 300), ("VIEW_RESUMED", 300), ("VIEW_FINISHED", 5400)]
    bodies = [event(user, "movie-1", t, ts, progress=p, session_id=f"session-{run_id}") for t, p in steps]
    for body in bodies:
        assert producer.post("/events", json=body).status_code == 202
    wait_ingested(clickhouse, [b["event_id"] for b in bodies])
    order = clickhouse.query(
        "SELECT event_type, progress_seconds FROM movie_events FINAL WHERE user_id = {u:String} "
        "ORDER BY kafka_partition, kafka_offset", parameters={"u": user}).result_rows
    assert order == steps
