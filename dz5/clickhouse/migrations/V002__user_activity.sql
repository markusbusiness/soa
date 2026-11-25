CREATE TABLE IF NOT EXISTS analytics.user_first_view
(
    user_id         String,
    first_view_date SimpleAggregateFunction(min, Date)
)
ENGINE = AggregatingMergeTree
ORDER BY user_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.user_first_view_mv TO analytics.user_first_view AS
SELECT user_id, min(toDate(timestamp)) AS first_view_date
FROM analytics.movie_events
WHERE event_type = 'VIEW_STARTED'
GROUP BY user_id;

CREATE TABLE IF NOT EXISTS analytics.user_activity_daily
(
    event_date Date,
    user_id    String
)
ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_date, user_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.user_activity_daily_mv TO analytics.user_activity_daily AS
SELECT DISTINCT toDate(timestamp) AS event_date, user_id
FROM analytics.movie_events;
