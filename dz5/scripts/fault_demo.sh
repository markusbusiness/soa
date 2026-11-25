#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."

P=http://localhost:${PRODUCER_PORT:-8010}
E=http://localhost:${EXPORTER_PORT:-8012}
YESTERDAY=$(date -u -d yesterday +%F)

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

describe() {
  docker compose exec -T kafka-1 kafka-topics --bootstrap-server kafka-1:9092 --describe --topic movie-events \
    | grep -E "Partition:" | awk '{print $3, $4, "leader="$6, "isr="$10}'
}

publish() {
  local started=$SECONDS
  local code
  code=$(curl -s -o /tmp/dz5-publish.json -w '%{http_code}' -X POST "$P/events" -H 'Content-Type: application/json' -d "{
    \"user_id\": \"fault-user-$RANDOM\", \"movie_id\": \"movie-0001\", \"event_type\": \"LIKED\",
    \"timestamp\": \"$(date -u +%FT%T.%3NZ)\", \"device_type\": \"MOBILE\", \"session_id\": \"fault\"}")
  echo "HTTP $code за $((SECONDS - started)) с: $(cat /tmp/dz5-publish.json)"
}

wait_healthy() {
  for service in "$@"; do
    until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q "$service")")" = healthy ]; do
      sleep 2
    done
  done
}

step "Исходное состояние: 3 брокера, RF=3, ISR из трёх реплик"
describe

step "Останавливаем kafka-2: его партиции получают лидеров на живых брокерах, ISR = 2 >= min.insync.replicas, запись продолжается"
docker compose stop kafka-2
sleep 5
describe
publish

step "Останавливаем kafka-3: остался один брокер, ISR = 1 < 2, acks=all не выполнить"
docker compose stop kafka-3
echo "(продюсер ретраит с exponential backoff, ответ придёт после исчерпания попыток)"
publish
docker compose logs --since 90s producer | grep -E "retry|failed" | tail -6

step "Возвращаем брокеры"
docker compose start kafka-2 kafka-3
wait_healthy kafka-2 kafka-3
sleep 5
describe
publish

CH=(docker compose exec -T clickhouse clickhouse-client -u analytics --password analytics -d analytics -q)
dlq_report() {
  "${CH[@]}" "SELECT
      (SELECT count() FROM movie_events_dlq) AS in_dlq,
      (SELECT countIf(outcome = 'recovered') FROM movie_events_dlq_resolution FINAL) AS recovered,
      (SELECT countIf(outcome = 'invalid') FROM movie_events_dlq_resolution FINAL) AS invalid,
      (SELECT count() FROM movie_events_dlq WHERE (kafka_topic, kafka_partition, kafka_offset) NOT IN
          (SELECT kafka_topic, kafka_partition, kafka_offset FROM movie_events_dlq_resolution)) AS unresolved
    FORMAT PrettyCompact"
}

lag() {
  docker compose exec -T kafka-1 kafka-consumer-groups --bootstrap-server kafka-1:9092 --describe \
    --group clickhouse-movie-events 2>/dev/null | awk 'NR > 1 && $6 ~ /^[0-9]+$/ {s += $6} END {print "отставание ClickHouse: " s + 0}'
}

step "Schema Registry недоступен, ClickHouse перезапущен (кеш схем пуст)"
docker compose stop schema-registry
docker compose restart clickhouse
wait_healthy clickhouse
echo "(30 с генератор пишет события, ClickHouse не может получить схему и не коммитит offset)"
sleep 30
lag

step "Schema Registry вернулся: отставание дочитывается; сообщения, попавшие в DLQ, восстанавливает dlq-recovery"
docker compose start schema-registry
wait_healthy schema-registry
sleep 40
lag
dlq_report
docker compose logs --since 60s dlq-recovery | grep "recovery run" | tail -2

step "MinIO недоступен: экспорт логирует ошибку, повтор при следующем запуске"
docker compose stop minio
curl -s -X POST "$E/export?date=$YESTERDAY"; echo
docker compose logs --since 60s exporter | grep -E "failed" | tail -3
docker compose start minio
wait_healthy minio
curl -s -X POST "$E/export?date=$YESTERDAY"; echo
