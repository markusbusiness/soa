#!/usr/bin/env bash
# Retry + Circuit Breaker (Flight Service down) and Redis Sentinel failover on the running stack.
set -euo pipefail
cd "$(dirname "$0")/.."

BASE_URL=${BASE_URL:-http://localhost:8000}
FLIGHT=11111111-1111-1111-1111-111111111101

step() { printf '\n=== %s\n' "$*"; }
get_flight() { curl -s -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" "$BASE_URL/flights/$FLIGHT"; }
master() { docker compose exec -T redis-sentinel-1 redis-cli -p 26379 sentinel get-master-addr-by-name mymaster | paste -sd: -; }
master_service() { case "$(master)" in *.10:*) echo redis-master ;; *) echo redis-replica ;; esac; }

step "Flight Service stopped: each request is retried 3 times, then the circuit opens"
since=$(date +%s)
docker compose stop flight-service >/dev/null 2>&1
for _ in 1 2 3 4 5 6 7; do get_flight; done
curl -s -D - -o /dev/null "$BASE_URL/flights/$FLIGHT" | grep -i retry-after
docker compose logs --since "$since" booking-service | grep -E "retry|circuit" | sed 's/^[^|]*| //' | tail -8

step "Flight Service started again, waiting for OPEN -> HALF_OPEN"
docker compose start flight-service >/dev/null 2>&1
sleep "${CB_OPEN_TIMEOUT_SECONDS:-15}"
sleep 2
get_flight
docker compose logs --since "$since" booking-service | grep circuit | sed 's/^[^|]*| //'

old_master=$(master_service)
step "Redis master before failover: $(master) ($old_master), stopping it"
get_flight
since=$(date +%s)
docker compose stop "$old_master" >/dev/null 2>&1
for _ in $(seq 1 8); do get_flight; sleep 1; done
echo "Redis master after failover: $(master)"
docker compose logs --since "$since" redis-sentinel-1 | grep -E "sdown|odown|switch-master" | sed 's/^[^|]*| //'
docker compose logs --since "$since" flight-service | grep cache | sed 's/^[^|]*| //' | tail -4

step "Old master $old_master comes back as a replica"
docker compose start "$old_master" >/dev/null 2>&1
sleep 15
docker compose exec -T "$old_master" redis-cli info replication | grep -E "^role|^master_host"
