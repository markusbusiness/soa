#!/bin/sh
set -eu

ch() {
  clickhouse-client --host "${CLICKHOUSE_HOST}" --user "${CLICKHOUSE_USER}" --password "${CLICKHOUSE_PASSWORD}" "$@"
}

for attempt in $(seq 1 60); do
  ch --query "SELECT 1" >/dev/null 2>&1 && break
  echo "clickhouse not ready, retry ${attempt}"
  sleep 2
done

ch --query "CREATE DATABASE IF NOT EXISTS analytics"
ch --query "CREATE TABLE IF NOT EXISTS analytics.schema_migrations
            (version String, checksum String, applied_at DateTime DEFAULT now())
            ENGINE = MergeTree ORDER BY version"

for file in $(ls /migrations/V*.sql | sort); do
  version=$(basename "$file" .sql)
  checksum=$(sha256sum "$file" | cut -d' ' -f1)
  applied=$(ch --query "SELECT checksum FROM analytics.schema_migrations WHERE version = '${version}'")
  if [ -n "$applied" ]; then
    if [ "$applied" != "$checksum" ]; then
      echo "checksum mismatch for applied migration ${version}" >&2
      exit 1
    fi
    echo "skip ${version}"
    continue
  fi
  echo "apply ${version}"
  ch --multiquery < "$file"
  ch --query "INSERT INTO analytics.schema_migrations (version, checksum) VALUES ('${version}', '${checksum}')"
done
