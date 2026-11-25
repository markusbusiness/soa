import os
import random
import uuid
from datetime import datetime, timezone

from confluent_kafka import Producer

from conftest import serialize_with_version, wait_for, wait_ingested


def resolution(clickhouse, partition: int, offset: int):
    rows = clickhouse.query(
        "SELECT outcome, detail FROM movie_events_dlq_resolution FINAL "
        "WHERE kafka_topic = 'movie-events' AND kafka_partition = {p:UInt16} AND kafka_offset = {o:UInt64}",
        parameters={"p": partition, "o": offset}).result_rows
    return rows[0] if rows else None


def test_broken_message_goes_to_dlq_and_does_not_block_ingestion(producer, clickhouse, run_id):
    delivered = {}

    def on_delivery(err, msg):
        delivered["partition"], delivered["offset"] = msg.partition(), msg.offset()

    kafka = Producer({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"], "acks": "all"})
    kafka.produce("movie-events", key=f"user-{run_id}".encode(), value=b"definitely not avro",
                  on_delivery=on_delivery)
    assert kafka.flush(15) == 0
    partition, offset = delivered["partition"], delivered["offset"]

    wait_for(lambda: clickhouse.query(
        "SELECT count() FROM movie_events_dlq WHERE kafka_partition = {p:UInt16} AND kafka_offset = {o:UInt64}",
        parameters={"p": partition, "o": offset}).result_rows[0][0] == 1, timeout=60, message="message in DLQ")
    outcome = wait_for(lambda: resolution(clickhouse, partition, offset), timeout=60, message="DLQ resolution")
    assert outcome[0] == "invalid"

    body = {"user_id": f"user-{run_id}", "movie_id": "movie-1", "event_type": "LIKED",
            "timestamp": datetime.now(timezone.utc).isoformat(), "device_type": "TV", "session_id": "s"}
    response = producer.post("/events", json=body)
    assert response.status_code == 202
    wait_ingested(clickhouse, [response.json()["event_id"]])


def test_valid_message_in_dlq_is_recovered(clickhouse, run_id):
    ts = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
    record = {"event_id": str(uuid.uuid4()), "user_id": f"user-{run_id}", "movie_id": f"movie-{run_id}",
              "event_type": "SEARCHED", "timestamp": ts, "device_type": "DESKTOP", "session_id": f"s-{run_id}",
              "progress_seconds": None, "search_query": "драма"}
    partition, offset = 900, random.randrange(10**12)
    clickhouse.insert("movie_events_dlq", [["movie-events", partition, offset, serialize_with_version(2, record),
                                            "Not found address of host: schema-registry: while fetching schema id"]],
                      column_names=["kafka_topic", "kafka_partition", "kafka_offset", "raw_message", "error"])

    outcome = wait_for(lambda: resolution(clickhouse, partition, offset), timeout=60, message="DLQ resolution")
    assert outcome == ("recovered", record["event_id"])
    row = clickhouse.query(
        "SELECT user_id, event_type, timestamp, search_query, kafka_partition, kafka_offset FROM movie_events FINAL "
        "WHERE event_id = {id:UUID}", parameters={"id": record["event_id"]}).result_rows[0]
    assert row == (f"user-{run_id}", "SEARCHED", ts, "драма", partition, offset)
