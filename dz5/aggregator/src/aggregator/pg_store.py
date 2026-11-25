from dataclasses import dataclass
from datetime import datetime

import asyncpg

from aggregator.clickhouse_jobs import DateAggregates

UPSERT_METRICS = """
INSERT INTO daily_metrics (metric_date, metric_name, value, computed_at, updated_at)
SELECT $1, name, value, $2, $2 FROM unnest($3::text[], $4::float8[]) AS t(name, value)
ON CONFLICT (metric_date, metric_name) DO UPDATE
SET value = EXCLUDED.value,
    computed_at = EXCLUDED.computed_at,
    updated_at = CASE WHEN daily_metrics.value IS DISTINCT FROM EXCLUDED.value
                      THEN EXCLUDED.updated_at ELSE daily_metrics.updated_at END
RETURNING updated_at = $2
"""

UPSERT_MOVIES = """
INSERT INTO top_movies (metric_date, rank, movie_id, views, unique_viewers, finished, likes, computed_at, updated_at)
SELECT $1, r, m, v, u, f, l, $2, $2
FROM unnest($3::int4[], $4::text[], $5::int8[], $6::int8[], $7::int8[], $8::int8[]) AS t(r, m, v, u, f, l)
ON CONFLICT (metric_date, rank) DO UPDATE
SET movie_id = EXCLUDED.movie_id, views = EXCLUDED.views, unique_viewers = EXCLUDED.unique_viewers,
    finished = EXCLUDED.finished, likes = EXCLUDED.likes, computed_at = EXCLUDED.computed_at,
    updated_at = CASE WHEN (top_movies.movie_id, top_movies.views, top_movies.unique_viewers,
                            top_movies.finished, top_movies.likes)
                           IS DISTINCT FROM
                           (EXCLUDED.movie_id, EXCLUDED.views, EXCLUDED.unique_viewers,
                            EXCLUDED.finished, EXCLUDED.likes)
                      THEN EXCLUDED.updated_at ELSE top_movies.updated_at END
RETURNING updated_at = $2
"""

UPSERT_DEVICES = """
INSERT INTO device_daily (metric_date, device_type, users, views, computed_at, updated_at)
SELECT $1, d, u, v, $2, $2 FROM unnest($3::text[], $4::int8[], $5::int8[]) AS t(d, u, v)
ON CONFLICT (metric_date, device_type) DO UPDATE
SET users = EXCLUDED.users, views = EXCLUDED.views, computed_at = EXCLUDED.computed_at,
    updated_at = CASE WHEN (device_daily.users, device_daily.views) IS DISTINCT FROM (EXCLUDED.users, EXCLUDED.views)
                      THEN EXCLUDED.updated_at ELSE device_daily.updated_at END
RETURNING updated_at = $2
"""

UPSERT_COHORT = """
INSERT INTO retention_cohorts (cohort_date, day_offset, cohort_size, retained_users, retention_rate,
                               computed_at, updated_at)
SELECT $1, o, s, r, rate, $2, $2 FROM unnest($3::int2[], $4::int8[], $5::int8[], $6::float8[]) AS t(o, s, r, rate)
ON CONFLICT (cohort_date, day_offset) DO UPDATE
SET cohort_size = EXCLUDED.cohort_size, retained_users = EXCLUDED.retained_users,
    retention_rate = EXCLUDED.retention_rate, computed_at = EXCLUDED.computed_at,
    updated_at = CASE WHEN (retention_cohorts.cohort_size, retention_cohorts.retained_users,
                            retention_cohorts.retention_rate)
                           IS DISTINCT FROM
                           (EXCLUDED.cohort_size, EXCLUDED.retained_users, EXCLUDED.retention_rate)
                      THEN EXCLUDED.updated_at ELSE retention_cohorts.updated_at END
RETURNING updated_at = $2
"""

UPSERT_DATE = """
INSERT INTO aggregate_dates (metric_date, computed_at, updated_at) VALUES ($1, $2, $2)
ON CONFLICT (metric_date) DO UPDATE
SET computed_at = EXCLUDED.computed_at,
    updated_at = CASE WHEN $3 THEN EXCLUDED.updated_at ELSE aggregate_dates.updated_at END
"""


@dataclass(frozen=True)
class WriteResult:
    rows_written: int
    changed: bool


class PostgresStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def ping(self) -> None:
        await self._pool.fetchval("SELECT 1")

    async def watermark(self) -> datetime | None:
        return await self._pool.fetchval("SELECT ingested_watermark FROM aggregation_state")

    async def save_watermark(self, value: datetime) -> None:
        await self._pool.execute(
            "INSERT INTO aggregation_state (id, ingested_watermark, updated_at) VALUES (true, $1, now()) "
            "ON CONFLICT (id) DO UPDATE SET ingested_watermark = GREATEST(aggregation_state.ingested_watermark, "
            "EXCLUDED.ingested_watermark), updated_at = now()", value)

    async def write(self, agg: DateAggregates) -> WriteResult:
        d, ts = agg.metric_date, agg.computed_at
        async with self._pool.acquire() as conn, conn.transaction():
            changed_flags: list[bool] = []

            rows = await conn.fetch(UPSERT_METRICS, d, ts, [m[0] for m in agg.metrics], [m[1] for m in agg.metrics])
            changed_flags += [r[0] for r in rows]

            columns = list(zip(*agg.movies)) or [[]] * 6
            rows = await conn.fetch(UPSERT_MOVIES, d, ts, *[list(c) for c in columns])
            changed_flags += [r[0] for r in rows]
            deleted = await conn.execute("DELETE FROM top_movies WHERE metric_date = $1 AND rank > $2",
                                         d, len(agg.movies))
            changed_flags.append(deleted != "DELETE 0")

            columns = list(zip(*agg.devices)) or [[]] * 3
            rows = await conn.fetch(UPSERT_DEVICES, d, ts, *[list(c) for c in columns])
            changed_flags += [r[0] for r in rows]
            deleted = await conn.execute("DELETE FROM device_daily WHERE metric_date = $1 "
                                         "AND NOT (device_type = ANY($2::text[]))", d, [x[0] for x in agg.devices])
            changed_flags.append(deleted != "DELETE 0")

            columns = list(zip(*agg.cohort)) or [[]] * 4
            rows = await conn.fetch(UPSERT_COHORT, d, ts, *[list(c) for c in columns])
            changed_flags += [r[0] for r in rows]

            changed = any(changed_flags)
            await conn.execute(UPSERT_DATE, d, ts, changed)
            rows_written = len(agg.metrics) + len(agg.movies) + len(agg.devices) + len(agg.cohort)
            return WriteResult(rows_written=rows_written, changed=changed)
