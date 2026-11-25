import uuid
from datetime import datetime, timezone

import pytest

from conftest import event, wait_ingested

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def valid(**overrides) -> dict:
    body = event(f"user-{uuid.uuid4().hex[:8]}", "movie-1", "VIEW_STARTED", NOW, progress=0)
    body.update(overrides)
    return body


@pytest.mark.parametrize("body", [
    pytest.param({k: v for k, v in valid().items() if k != "user_id"}, id="missing-user_id"),
    pytest.param(valid(event_type="VIEW_SKIPPED"), id="unknown-event_type"),
    pytest.param(valid(device_type="FRIDGE"), id="unknown-device_type"),
    pytest.param(valid(event_id="not-a-uuid"), id="bad-event_id"),
    pytest.param(valid(timestamp="2026-01-15T12:00:00"), id="timestamp-without-timezone"),
    pytest.param(valid(progress_seconds=-5), id="negative-progress"),
    pytest.param(valid(progress_seconds=2**31), id="progress-overflows-int32"),
    pytest.param({k: v for k, v in valid().items() if k != "progress_seconds"}, id="view-event-without-progress"),
    pytest.param(valid(user_id=""), id="empty-user_id"),
    pytest.param(valid(rating=5), id="unknown-field"),
    pytest.param(valid(search_query="комедия"), id="search_query-on-view-event"),
])
def test_invalid_event_rejected(producer, body):
    response = producer.post("/events", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "VALIDATION_ERROR"


def test_valid_event_returns_its_id(producer):
    body = valid()
    response = producer.post("/events", json=body)
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["event_id"] == body["event_id"]
    assert payload["partition"] >= 0 and payload["offset"] >= 0


def test_event_id_generated_when_absent(producer):
    body = valid()
    del body["event_id"]
    response = producer.post("/events", json=body)
    assert response.status_code == 202, response.text
    uuid.UUID(response.json()["event_id"])


def test_non_view_event_without_progress_accepted(producer):
    response = producer.post("/events", json=valid(event_type="SEARCHED", progress_seconds=None))
    assert response.status_code == 202, response.text


def test_search_event_with_query_accepted(producer):
    body = valid(event_type="SEARCHED", progress_seconds=None, search_query="фантастика космос")
    assert producer.post("/events", json=body).status_code == 202


def test_timestamp_converted_to_utc(producer, clickhouse):
    body = valid(timestamp="2026-01-15T15:00:00+03:00")
    assert producer.post("/events", json=body).status_code == 202
    wait_ingested(clickhouse, [body["event_id"]])
    stored = clickhouse.query("SELECT timestamp FROM movie_events WHERE event_id = {id:UUID}",
                              parameters={"id": body["event_id"]}).result_rows[0][0]
    assert stored == NOW


def test_same_user_goes_to_same_partition(producer):
    user = f"user-{uuid.uuid4().hex[:8]}"
    partitions = {
        producer.post("/events", json=valid(user_id=user, movie_id=f"movie-{i}")).json()["partition"]
        for i in range(10)
    }
    assert len(partitions) == 1


def test_users_spread_across_partitions(producer):
    partitions = {producer.post("/events", json=valid()).json()["partition"] for _ in range(40)}
    assert len(partitions) >= 3
