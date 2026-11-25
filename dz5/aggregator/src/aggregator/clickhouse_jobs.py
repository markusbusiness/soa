from dataclasses import dataclass
from datetime import date, datetime

import clickhouse_connect

from aggregator.config import Settings

COHORT_SQL = """
INSERT INTO agg_retention_cohort (cohort_date, day_offset, cohort_size, retained_users, retention_rate, computed_at)
WITH
    cohort_users AS (
        SELECT user_id
        FROM user_first_view
        GROUP BY user_id
        HAVING min(first_view_date) = {d:Date}
    ),
    sizes AS (SELECT count() AS size FROM cohort_users),
    activity AS (
        SELECT toUInt8(dateDiff('day', {d:Date}, event_date)) AS day_offset,
               uniqExact(user_id) AS retained
        FROM user_activity_daily
        WHERE event_date BETWEEN {d:Date} AND {d:Date} + 7
          AND user_id IN (SELECT user_id FROM cohort_users)
        GROUP BY day_offset
    )
SELECT {d:Date},
       offsets.day_offset,
       sizes.size,
       activity.retained,
       if(sizes.size = 0, NULL, activity.retained / sizes.size),
       {ts:DateTime64(3)}
FROM (SELECT toUInt8(arrayJoin(range(0, {max_offset:UInt8} + 1))) AS day_offset) AS offsets
CROSS JOIN sizes
LEFT JOIN activity ON activity.day_offset = offsets.day_offset
"""

DAILY_SQL = """
INSERT INTO agg_daily_metrics (metric_date, metric_name, value, computed_at)
WITH
    day AS (
        SELECT count() AS events_total,
               uniqExact(user_id) AS dau,
               uniqExact(session_id) AS sessions,
               countIf(event_type = 'VIEW_STARTED') AS views_started,
               countIf(event_type = 'VIEW_FINISHED') AS views_finished,
               avgIf(progress_seconds, event_type = 'VIEW_FINISHED') AS avg_finished_progress,
               countIf(event_type = 'LIKED') AS likes,
               countIf(event_type = 'SEARCHED') AS searches
        FROM movie_events FINAL
        WHERE event_date = {d:Date}
    ),
    rolling AS (
        SELECT uniqExactIf(user_id, event_date > {d:Date} - 7) AS wau,
               uniqExact(user_id) AS mau
        FROM user_activity_daily
        WHERE event_date BETWEEN {d:Date} - 29 AND {d:Date}
    ),
    cohort AS (
        SELECT anyIf(cohort_size, day_offset = 0) AS new_viewers,
               anyIf(retention_rate, day_offset = 1) AS d1,
               anyIf(retention_rate, day_offset = 7) AS d7
        FROM agg_retention_cohort FINAL
        WHERE cohort_date = {d:Date}
    )
SELECT {d:Date}, metric.1, metric.2, {ts:DateTime64(3)}
FROM day CROSS JOIN rolling CROSS JOIN cohort
ARRAY JOIN [
    ('events_total',     CAST(events_total AS Nullable(Float64))),
    ('dau',              CAST(dau AS Nullable(Float64))),
    ('wau',              CAST(wau AS Nullable(Float64))),
    ('mau',              CAST(mau AS Nullable(Float64))),
    ('sessions',         CAST(sessions AS Nullable(Float64))),
    ('views_started',    CAST(views_started AS Nullable(Float64))),
    ('views_finished',   CAST(views_finished AS Nullable(Float64))),
    ('view_conversion',  if(views_started = 0, NULL, views_finished / views_started)),
    ('avg_view_seconds', if(views_finished = 0, NULL, CAST(avg_finished_progress AS Nullable(Float64)))),
    ('likes',            CAST(likes AS Nullable(Float64))),
    ('searches',         CAST(searches AS Nullable(Float64))),
    ('new_viewers',      CAST(new_viewers AS Nullable(Float64))),
    ('retention_d1',     CAST(d1 AS Nullable(Float64))),
    ('retention_d7',     CAST(d7 AS Nullable(Float64)))
] AS metric
"""

MOVIES_SQL = """
INSERT INTO agg_movie_daily (metric_date, movie_id, views, unique_viewers, finished, likes, rank, computed_at)
SELECT {d:Date}, movie_id, views, unique_viewers, finished, likes,
       row_number() OVER (ORDER BY views DESC, unique_viewers DESC, movie_id),
       {ts:DateTime64(3)}
FROM (
    SELECT movie_id,
           countIf(event_type = 'VIEW_STARTED') AS views,
           uniqExactIf(user_id, event_type = 'VIEW_STARTED') AS unique_viewers,
           countIf(event_type = 'VIEW_FINISHED') AS finished,
           countIf(event_type = 'LIKED') AS likes
    FROM movie_events FINAL
    WHERE event_date = {d:Date}
    GROUP BY movie_id
)
"""

DEVICES_SQL = """
INSERT INTO agg_device_daily (metric_date, device_type, users, views, computed_at)
SELECT {d:Date}, device_type, uniqExact(user_id), countIf(event_type = 'VIEW_STARTED'), {ts:DateTime64(3)}
FROM movie_events FINAL
WHERE event_date = {d:Date}
GROUP BY device_type
"""


@dataclass(frozen=True)
class DateAggregates:
    metric_date: date
    computed_at: datetime
    metrics: list[tuple[str, float | None]]
    movies: list[tuple]
    devices: list[tuple]
    cohort: list[tuple]

    @property
    def events_processed(self) -> int:
        return int(dict(self.metrics).get("events_total") or 0)


class ClickHouseAggregates:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = clickhouse_connect.get_client(
            host=settings.clickhouse_host, port=settings.clickhouse_port,
            username=settings.clickhouse_user, password=settings.clickhouse_password,
            database=settings.clickhouse_database, autogenerate_session_id=False, tz_mode="aware",
        )

    def ping(self) -> bool:
        return self._client.ping()

    def touched_dates(self, since: datetime | None) -> tuple[list[date], datetime | None]:
        """Даты событий, пришедших в ClickHouse начиная с since, и максимальный ingested_at."""
        where = "" if since is None else "WHERE ingested_at >= {since:DateTime64(3)}"
        rows = self._client.query(
            f"SELECT event_date, max(ingested_at) FROM movie_events {where} GROUP BY event_date",
            parameters={"since": _ch_time(since)} if since else None,
        ).result_rows
        watermark = max((row[1] for row in rows), default=None)
        return sorted(row[0] for row in rows), watermark

    def dates_with_events(self, start: date, end: date) -> list[date]:
        rows = self._client.query(
            "SELECT DISTINCT event_date FROM user_activity_daily WHERE event_date BETWEEN {start:Date} AND {end:Date}",
            parameters={"start": start, "end": end},
        ).result_rows
        return sorted(row[0] for row in rows)

    def compute(self, metric_date: date, today: date, computed_at: datetime, top_limit: int) -> DateAggregates:
        params = {
            "d": metric_date,
            "ts": _ch_time(computed_at),
            "max_offset": min(7, (today - metric_date).days),
        }
        for sql in (COHORT_SQL, DAILY_SQL, MOVIES_SQL, DEVICES_SQL):
            self._client.command(sql, parameters=params)

        query = self._client.query
        return DateAggregates(
            metric_date=metric_date,
            computed_at=computed_at,
            metrics=query(
                "SELECT metric_name, value FROM agg_daily_metrics FINAL "
                "WHERE metric_date = {d:Date} ORDER BY metric_name", parameters=params).result_rows,
            movies=query(
                "SELECT rank, movie_id, views, unique_viewers, finished, likes FROM agg_movie_daily FINAL "
                "WHERE metric_date = {d:Date} AND rank <= {limit:UInt32} ORDER BY rank",
                parameters={**params, "limit": top_limit}).result_rows,
            devices=query(
                "SELECT toString(device_type), users, views FROM agg_device_daily FINAL "
                "WHERE metric_date = {d:Date} ORDER BY device_type", parameters=params).result_rows,
            cohort=query(
                "SELECT day_offset, cohort_size, retained_users, retention_rate FROM agg_retention_cohort FINAL "
                "WHERE cohort_date = {d:Date} AND day_offset <= {max_offset:UInt8} ORDER BY day_offset",
                parameters=params).result_rows,
        )


def _ch_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
