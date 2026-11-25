import io
import json
import logging
import struct
import threading
from dataclasses import dataclass

import clickhouse_connect
import httpx
from fastavro import parse_schema, schemaless_reader

log = logging.getLogger("dlq-recovery")

EVENT_COLUMNS = ["event_id", "user_id", "movie_id", "event_type", "timestamp", "device_type", "session_id",
                 "progress_seconds", "search_query", "kafka_partition", "kafka_offset"]

PENDING_SQL = """
SELECT kafka_topic, kafka_partition, kafka_offset, any(raw_message) AS raw_message
FROM movie_events_dlq
WHERE (kafka_topic, kafka_partition, kafka_offset) NOT IN (
    SELECT kafka_topic, kafka_partition, kafka_offset FROM movie_events_dlq_resolution
)
GROUP BY kafka_topic, kafka_partition, kafka_offset
ORDER BY kafka_partition, kafka_offset
LIMIT {limit:UInt32}
"""


class InvalidMessage(Exception):
    pass


class RegistryUnavailable(Exception):
    pass


@dataclass
class RunResult:
    pending: int = 0
    recovered: int = 0
    invalid: int = 0
    deferred: int = 0


class SchemaCache:
    def __init__(self, registry_url: str):
        self._http = httpx.Client(base_url=registry_url, timeout=5)
        self._schemas: dict[int, dict] = {}

    def get(self, schema_id: int) -> dict:
        if schema_id not in self._schemas:
            try:
                response = self._http.get(f"/schemas/ids/{schema_id}")
            except httpx.HTTPError as exc:
                raise RegistryUnavailable(repr(exc)) from exc
            if response.status_code == 404:
                raise InvalidMessage(f"unknown schema id {schema_id}")
            if response.status_code != 200:
                raise RegistryUnavailable(f"HTTP {response.status_code}")
            self._schemas[schema_id] = parse_schema(json.loads(response.json()["schema"]))
        return self._schemas[schema_id]


def decode(raw: bytes, schemas: SchemaCache) -> dict:
    if not isinstance(raw, bytes):
        raise TypeError(f"raw_message must be read as bytes, got {type(raw).__name__}")
    if len(raw) < 5 or raw[0] != 0:
        raise InvalidMessage("not a Confluent Avro message (magic byte)")
    schema_id = struct.unpack(">I", raw[1:5])[0]
    schema = schemas.get(schema_id)
    try:
        return schemaless_reader(io.BytesIO(raw[5:]), schema)
    except Exception as exc:
        raise InvalidMessage(f"avro decode failed: {exc!r}") from exc


class DlqRecovery:
    def __init__(self, host: str, user: str, password: str, registry_url: str, batch_size: int):
        self._ch = clickhouse_connect.get_client(host=host, username=user, password=password, database="analytics",
                                                 autogenerate_session_id=False, tz_mode="aware")
        self._schemas = SchemaCache(registry_url)
        self._batch_size = batch_size
        self._lock = threading.Lock()

    def ping(self) -> bool:
        return self._ch.ping()

    def run_once(self) -> RunResult:
        with self._lock:
            return self._run()

    def _run(self) -> RunResult:
        rows = self._ch.query(PENDING_SQL, parameters={"limit": self._batch_size},
                              column_formats={"raw_message": "bytes"}).result_rows
        result = RunResult(pending=len(rows))
        events, resolutions = [], []
        for topic, partition, offset, raw in rows:
            try:
                record = decode(raw, self._schemas)
            except RegistryUnavailable as exc:
                result.deferred += 1
                log.warning("dlq message deferred partition=%d offset=%d: schema registry unavailable (%s)",
                            partition, offset, exc)
                continue
            except InvalidMessage as exc:
                result.invalid += 1
                resolutions.append([topic, partition, offset, "invalid", str(exc)])
                log.error("dlq message invalid partition=%d offset=%d: %s", partition, offset, exc)
                continue
            events.append([record["event_id"], record["user_id"], record["movie_id"], record["event_type"],
                           record["timestamp"], record["device_type"], record["session_id"],
                           record.get("progress_seconds"), record.get("search_query"), partition, offset])
            resolutions.append([topic, partition, offset, "recovered", str(record["event_id"])])
            result.recovered += 1
        if events:
            self._ch.insert("movie_events", events, column_names=EVENT_COLUMNS)
        if resolutions:
            self._ch.insert("movie_events_dlq_resolution", resolutions,
                            column_names=["kafka_topic", "kafka_partition", "kafka_offset", "outcome", "detail"])
        if result.pending:
            log.info("dlq recovery run pending=%d recovered=%d invalid=%d deferred=%d",
                     result.pending, result.recovered, result.invalid, result.deferred)
        return result
