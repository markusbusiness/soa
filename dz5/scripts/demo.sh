#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

P=http://localhost:${PRODUCER_PORT:-8010}
A=http://localhost:${AGGREGATOR_PORT:-8011}
E=http://localhost:${EXPORTER_PORT:-8012}
CH=(docker compose exec -T clickhouse clickhouse-client -u analytics --password analytics -d analytics -q)
TODAY=$(date -u +%F)
YESTERDAY=$(date -u -d yesterday +%F)

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "Публикуем событие через HTTP API продюсера"
EVENT_ID=$(cat /proc/sys/kernel/random/uuid)
curl -s -X POST "$P/events" -H 'Content-Type: application/json' -d "{
  \"event_id\": \"$EVENT_ID\", \"user_id\": \"demo-user\", \"movie_id\": \"movie-0001\",
  \"event_type\": \"VIEW_STARTED\", \"timestamp\": \"$(date -u +%FT%T.%3NZ)\",
  \"device_type\": \"TV\", \"session_id\": \"demo-session\", \"progress_seconds\": 0}" | jq .

step "Невалидное событие: VIEW_FINISHED без progress_seconds и неизвестное устройство"
curl -s -X POST "$P/events" -H 'Content-Type: application/json' -d '{
  "user_id": "demo-user", "movie_id": "movie-0001", "event_type": "VIEW_FINISHED",
  "timestamp": "2026-11-20T10:00:00Z", "device_type": "FRIDGE", "session_id": "s"}' | jq .

step "Ждём событие в ClickHouse"
for _ in $(seq 1 30); do
  [ "$("${CH[@]}" "SELECT count() FROM movie_events WHERE event_id = '$EVENT_ID'")" = 1 ] && break
  sleep 1
done
"${CH[@]}" "SELECT * FROM movie_events WHERE event_id = '$EVENT_ID' FORMAT Vertical"

step "Сколько событий в хранилище по дням"
"${CH[@]}" "SELECT event_date, count() AS events, uniqExact(user_id) AS users FROM movie_events GROUP BY event_date ORDER BY event_date FORMAT PrettyCompact"

step "Ручной пересчёт агрегатов за $YESTERDAY"
curl -s -X POST "$A/recompute?date=$YESTERDAY" | jq .

step "Метрики за $YESTERDAY из PostgreSQL"
curl -s "$A/metrics?date=$YESTERDAY" | jq '{date, computed_at, metrics, top_movies: .top_movies[:3], retention_cohort}'

step "Ручной экспорт за $YESTERDAY в S3 (дважды: файл перезаписывается)"
curl -s -X POST "$E/export?date=$YESTERDAY" | jq .
curl -s -X POST "$E/export?date=$YESTERDAY" | jq .
docker compose exec -T minio sh -c 'mc alias set local http://localhost:9000 minio minio-secret >/dev/null && mc ls -r local/movie-analytics'

step "Retention-когорты (то же, что в панели Grafana)"
"${CH[@]}" "SELECT cohort_date, max(cohort_size) AS size,
  $(for k in 0 1 2 3 4 5 6 7; do printf "round(100 * maxIf(retention_rate, day_offset = %d)) AS d%d%s" $k $k "$([ $k -lt 7 ] && echo ,)"; done)
  FROM agg_retention_cohort FINAL WHERE cohort_size > 0 GROUP BY cohort_date ORDER BY cohort_date FORMAT PrettyCompact"

echo
echo "Grafana: http://localhost:${GRAFANA_PORT:-3030}/d/movie-analytics (admin/admin), MinIO: http://localhost:${MINIO_CONSOLE_PORT:-9001} (minio/minio-secret), сегодня $TODAY"
