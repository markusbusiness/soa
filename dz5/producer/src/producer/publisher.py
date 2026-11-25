import asyncio
import json
import logging
import random
import threading
from dataclasses import dataclass

from confluent_kafka import KafkaError, KafkaException, Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext
from fastavro import parse_schema
from fastavro.validation import ValidationError, validate

from producer.config import PublisherSettings

log = logging.getLogger("producer.publisher")

RETRYABLE_CODES = {
    KafkaError._MSG_TIMED_OUT,
    KafkaError._TIMED_OUT,
    KafkaError._TRANSPORT,
    KafkaError._ALL_BROKERS_DOWN,
    KafkaError._QUEUE_FULL,
    KafkaError.NOT_ENOUGH_REPLICAS,
    KafkaError.NOT_ENOUGH_REPLICAS_AFTER_APPEND,
    KafkaError.LEADER_NOT_AVAILABLE,
    KafkaError.NOT_LEADER_FOR_PARTITION,
    KafkaError.REQUEST_TIMED_OUT,
}


class EventValidationError(Exception):
    pass


class PublishError(Exception):
    pass


class _RetryableError(Exception):
    pass


@dataclass(frozen=True)
class Delivery:
    event_id: str
    partition: int
    offset: int


class KafkaPublisher:
    def __init__(self, settings: PublisherSettings):
        self._settings = settings
        self._registry = SchemaRegistryClient({"url": settings.schema_registry_url})
        latest = self._registry.get_latest_version(settings.subject)
        self.schema_id = latest.schema_id
        self.schema_version = latest.version
        self._parsed_schema = parse_schema(json.loads(latest.schema.schema_str))
        self._serializer = AvroSerializer(
            self._registry,
            latest.schema.schema_str,
            conf={"auto.register.schemas": False, "use.latest.version": True},
        )
        self._producer = Producer({
            "bootstrap.servers": settings.bootstrap_servers,
            "client.id": "movie-events-producer",
            "acks": "all",
            "enable.idempotence": True,
            "max.in.flight.requests.per.connection": 5,
            "retries": 2147483647,
            "retry.backoff.ms": 100,
            "retry.backoff.max.ms": 1000,
            "delivery.timeout.ms": settings.delivery_timeout_ms,
            "linger.ms": 5,
            "compression.type": "zstd",
        })
        self._running = True
        self._poller = threading.Thread(target=self._poll_loop, name="kafka-poll", daemon=True)
        self._poller.start()
        log.info("publisher ready topic=%s subject=%s schema_id=%s version=%s",
                 settings.topic, settings.subject, self.schema_id, self.schema_version)

    def _poll_loop(self) -> None:
        while self._running:
            self._producer.poll(0.1)

    def validate(self, record: dict) -> None:
        try:
            validate(record, self._parsed_schema, raise_errors=True)
        except ValidationError as exc:
            raise EventValidationError(str(exc)) from exc

    def is_ready(self) -> bool:
        try:
            metadata = self._producer.list_topics(self._settings.topic, timeout=3)
        except KafkaException:
            return False
        topic = metadata.topics.get(self._settings.topic)
        return topic is not None and topic.error is None and len(topic.partitions) > 0

    async def publish(self, record: dict) -> Delivery:
        self.validate(record)
        attempts = self._settings.max_attempts
        for attempt in range(1, attempts + 1):
            try:
                delivery = await self._produce_once(record)
            except _RetryableError as exc:
                if attempt == attempts:
                    log.error("event publish failed event_id=%s attempts=%d error=%s",
                              record["event_id"], attempt, exc)
                    raise PublishError(str(exc)) from exc
                delay = min(self._settings.backoff_max_seconds,
                            self._settings.backoff_base_seconds * 2 ** (attempt - 1))
                delay = random.uniform(delay / 2, delay)
                log.warning("event publish retry event_id=%s attempt=%d/%d delay=%.2fs error=%s",
                            record["event_id"], attempt, attempts, delay, exc)
                await asyncio.sleep(delay)
                continue
            log.info("event published event_id=%s event_type=%s timestamp=%s user_id=%s partition=%d offset=%d",
                     record["event_id"], record["event_type"], record["timestamp"].isoformat(),
                     record["user_id"], delivery.partition, delivery.offset)
            return delivery
        raise AssertionError("unreachable")

    async def _produce_once(self, record: dict) -> Delivery:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Delivery] = loop.create_future()

        try:
            value = self._serializer(record, SerializationContext(self._settings.topic, MessageField.VALUE))
        except Exception as exc:
            raise _RetryableError(f"serialization/schema registry: {exc}") from exc

        def resolve(err, msg) -> None:
            if future.done():
                return
            if err is not None:
                future.set_exception(self._classify(err))
            else:
                future.set_result(Delivery(record["event_id"], msg.partition(), msg.offset()))

        def on_delivery(err, msg) -> None:
            loop.call_soon_threadsafe(resolve, err, msg)

        try:
            self._producer.produce(self._settings.topic, key=record["user_id"].encode(), value=value,
                                   on_delivery=on_delivery)
        except BufferError as exc:
            raise _RetryableError(f"local queue full: {exc}") from exc
        except KafkaException as exc:
            raise self._classify(exc.args[0]) from exc
        return await future

    @staticmethod
    def _classify(err: KafkaError) -> Exception:
        if err.retriable() or err.code() in RETRYABLE_CODES:
            return _RetryableError(err.str())
        return PublishError(err.str())

    def close(self) -> None:
        remaining = self._producer.flush(10)
        self._running = False
        self._poller.join(timeout=2)
        if remaining:
            log.warning("producer closed with %d undelivered messages", remaining)
