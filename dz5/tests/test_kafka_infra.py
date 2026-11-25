import json
import os

import httpx
from confluent_kafka.admin import AdminClient, ConfigResource

TOPIC = "movie-events"
SUBJECT = "movie-events-value"


def admin() -> AdminClient:
    return AdminClient({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"]})


def test_cluster_has_three_brokers():
    metadata = admin().list_topics(timeout=10)
    assert len(metadata.brokers) == 3


def test_topic_partitions_and_replication():
    metadata = admin().list_topics(TOPIC, timeout=10)
    partitions = metadata.topics[TOPIC].partitions
    assert len(partitions) >= 3
    for partition in partitions.values():
        assert len(partition.replicas) == 3
        assert len(partition.isrs) >= 2


def test_topic_min_insync_replicas():
    client = admin()
    resource = ConfigResource(ConfigResource.Type.TOPIC, TOPIC)
    configs = client.describe_configs([resource])[resource].result(timeout=10)
    assert configs["min.insync.replicas"].value == "2"


def test_schema_registered_with_version_and_compatibility():
    registry = os.environ["SCHEMA_REGISTRY_URL"]
    versions = httpx.get(f"{registry}/subjects/{SUBJECT}/versions").json()
    assert versions == [1, 2]
    latest = httpx.get(f"{registry}/subjects/{SUBJECT}/versions/latest").json()
    assert latest["version"] == 2
    compatibility = httpx.get(f"{registry}/config/{SUBJECT}").json()["compatibilityLevel"]
    assert compatibility == "BACKWARD"

    schema = json.loads(latest["schema"])
    fields = {f["name"]: f for f in schema["fields"]}
    assert set(fields) == {"event_id", "user_id", "movie_id", "event_type", "timestamp", "device_type",
                           "session_id", "progress_seconds", "search_query"}
    assert fields["search_query"]["default"] is None
    assert fields["event_id"]["type"]["logicalType"] == "uuid"
    assert fields["timestamp"]["type"]["logicalType"] == "timestamp-millis"
    assert fields["event_type"]["type"]["symbols"] == ["VIEW_STARTED", "VIEW_FINISHED", "VIEW_PAUSED",
                                                       "VIEW_RESUMED", "LIKED", "SEARCHED"]
    assert fields["device_type"]["type"]["symbols"] == ["MOBILE", "DESKTOP", "TV", "TABLET"]


def test_clickhouse_schema_matches_registry(clickhouse):
    registry = os.environ["SCHEMA_REGISTRY_URL"]
    schema = json.loads(httpx.get(f"{registry}/subjects/{SUBJECT}/versions/latest").json()["schema"])
    fields = {f["name"]: f for f in schema["fields"]}
    columns = dict(clickhouse.query(
        "SELECT name, type FROM system.columns WHERE database = 'analytics' AND table = 'movie_events_queue'"
    ).result_rows)
    assert set(columns) == set(fields)
    assert columns["event_id"] == "UUID"
    assert columns["timestamp"] == "DateTime64(3, 'UTC')"
    assert columns["progress_seconds"] == "Nullable(Int32)"
    for enum_field in ("event_type", "device_type"):
        for symbol in fields[enum_field]["type"]["symbols"]:
            assert f"'{symbol}'" in columns[enum_field]


def test_schema_v2_is_backward_compatible_with_v1():
    registry = os.environ["SCHEMA_REGISTRY_URL"]
    v1 = json.loads(httpx.get(f"{registry}/subjects/{SUBJECT}/versions/1").json()["schema"])
    v2 = json.loads(httpx.get(f"{registry}/subjects/{SUBJECT}/versions/2").json()["schema"])
    assert [f["name"] for f in v2["fields"]] == [f["name"] for f in v1["fields"]] + ["search_query"]
    check = httpx.post(f"{registry}/compatibility/subjects/{SUBJECT}/versions/1",
                       json={"schema": json.dumps(v2)},
                       headers={"Content-Type": "application/vnd.schemaregistry.v1+json"}).json()
    assert check["is_compatible"] is True
