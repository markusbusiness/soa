#!/bin/sh
set -eu

BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS}"
TOPIC="${TOPIC_NAME:-movie-events}"

kafka-topics --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
  --topic "$TOPIC" \
  --partitions "${TOPIC_PARTITIONS:-6}" \
  --replication-factor "${TOPIC_REPLICATION_FACTOR:-3}" \
  --config min.insync.replicas="${TOPIC_MIN_INSYNC_REPLICAS:-2}" \
  --config retention.ms="${TOPIC_RETENTION_MS:-604800000}"

kafka-configs --bootstrap-server "$BOOTSTRAP" --alter --entity-type topics --entity-name "$TOPIC" \
  --add-config min.insync.replicas="${TOPIC_MIN_INSYNC_REPLICAS:-2}"

kafka-topics --bootstrap-server "$BOOTSTRAP" --describe --topic "$TOPIC"
