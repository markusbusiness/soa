import os
import random
import time
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

import boto3
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext
import clickhouse_connect
import httpx
import psycopg
import pytest


def wait_for(predicate: Callable[[], object], timeout: float = 60, interval: float = 0.5, message: str = ""):
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result:
            return result
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout}s: {message}")
        time.sleep(interval)


@pytest.fixture(scope="session")
def producer():
    with httpx.Client(base_url=os.environ["PRODUCER_URL"], timeout=60) as client:
        yield client


@pytest.fixture(scope="session")
def aggregator():
    with httpx.Client(base_url=os.environ["AGGREGATOR_URL"], timeout=120) as client:
        yield client


@pytest.fixture(scope="session")
def exporter():
    with httpx.Client(base_url=os.environ["EXPORTER_URL"], timeout=60) as client:
        yield client


@pytest.fixture(scope="session")
def grafana():
    with httpx.Client(base_url=os.environ["GRAFANA_URL"], auth=("admin", "admin"), timeout=30) as client:
        yield client


@pytest.fixture(scope="session")
def clickhouse():
    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"], username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"], database="analytics", autogenerate_session_id=False,
        tz_mode="aware")
    yield client
    client.close()


@pytest.fixture(scope="session")
def postgres():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        yield conn


@pytest.fixture(scope="session")
def s3():
    return boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT_URL"], region_name="us-east-1",
                        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
                        aws_secret_access_key=os.environ["S3_SECRET_KEY"])


@pytest.fixture
def run_id() -> str:
    return uuid.uuid4().hex[:10]


def free_day(clickhouse) -> date:
    """Случайный день в прошлом без событий в окне [day-30, day+30]."""
    rng = random.Random()
    while True:
        day = date(2010, 1, 1) + timedelta(days=rng.randrange(365 * 12))
        busy = clickhouse.query(
            "SELECT count() FROM user_activity_daily WHERE event_date BETWEEN {d:Date} - 30 AND {d:Date} + 30",
            parameters={"d": day}).result_rows[0][0]
        if busy == 0:
            return day


def event(user_id: str, movie_id: str, event_type: str, ts: datetime, progress: int | None = None,
          session_id: str | None = None, device: str = "DESKTOP") -> dict:
    body = {
        "event_id": str(uuid.uuid4()),
        "user_id": user_id,
        "movie_id": movie_id,
        "event_type": event_type,
        "timestamp": ts.isoformat(),
        "device_type": device,
        "session_id": session_id or f"s-{user_id}-{ts.date()}",
    }
    if progress is not None:
        body["progress_seconds"] = progress
    return body


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def publish_all(producer: httpx.Client, events: list[dict]) -> None:
    for body in events:
        response = producer.post("/events", json=body)
        assert response.status_code == 202, response.text


def wait_ingested(clickhouse, event_ids: list[str], timeout: float = 90) -> None:
    def count() -> bool:
        found = clickhouse.query("SELECT uniqExact(event_id) FROM movie_events WHERE event_id IN {ids:Array(UUID)}",
                                 parameters={"ids": event_ids}).result_rows[0][0]
        return found == len(event_ids)

    wait_for(count, timeout=timeout, message=f"{len(event_ids)} events in ClickHouse")


@pytest.fixture(scope="session")
def cohort_dataset(producer, aggregator, clickhouse):
    """Когорта D = a, b, c, d; e только ищет. В D+1 возвращаются a, b, в D+7 возвращаются b, c."""
    day = free_day(clickhouse)
    run = uuid.uuid4().hex[:8]
    a, b, c, d, e = (f"user-{run}-{x}" for x in "abcde")
    m1, m2, m3 = (f"movie-{run}-{x}" for x in ("m1", "m2", "m3"))
    d1, d7 = day + timedelta(days=1), day + timedelta(days=7)
    events = [
        event(a, m1, "VIEW_STARTED", at(day, 10), 0, device="TV"),
        event(a, m1, "VIEW_FINISHED", at(day, 11), 3600, device="TV"),
        event(b, m1, "VIEW_STARTED", at(day, 12), 0),
        event(b, m1, "VIEW_PAUSED", at(day, 12, 5), 100),
        event(b, m1, "VIEW_RESUMED", at(day, 12, 10), 100),
        event(b, m1, "VIEW_FINISHED", at(day, 12, 40), 1800),
        event(c, m2, "VIEW_STARTED", at(day, 14), 0, device="MOBILE"),
        event(d, m3, "SEARCHED", at(day, 15), device="TABLET"),
        event(d, m3, "VIEW_STARTED", at(day, 15, 1), 0, device="TABLET"),
        event(d, m3, "VIEW_FINISHED", at(day, 15, 11), 600, device="TABLET"),
        event(d, m3, "LIKED", at(day, 15, 12), device="TABLET"),
        event(e, m3, "SEARCHED", at(day, 16), device="MOBILE"),
        event(a, m1, "VIEW_STARTED", at(d1, 9), 0, device="TV"),
        event(b, m2, "LIKED", at(d1, 9)),
        event(b, m2, "VIEW_STARTED", at(d7, 20), 0),
        event(c, m1, "VIEW_STARTED", at(d7, 21), 0, device="MOBILE"),
    ]
    publish_all(producer, events)
    wait_ingested(clickhouse, [x["event_id"] for x in events])
    response = aggregator.post("/recompute", params={"date": day.isoformat()})
    assert response.status_code == 200, response.text
    return {"day": day, "events": events, "movies": (m1, m2, m3), "recompute": response.json()}


def serialize_with_version(version: int, record: dict) -> bytes:
    """Confluent-Avro байты записи, сериализованной схемой указанной версии subject movie-events-value."""
    registry = SchemaRegistryClient({"url": os.environ["SCHEMA_REGISTRY_URL"]})
    schema = registry.get_version("movie-events-value", version).schema.schema_str
    serializer = AvroSerializer(registry, schema, conf={"auto.register.schemas": False})
    return serializer(record, SerializationContext("movie-events", MessageField.VALUE))
