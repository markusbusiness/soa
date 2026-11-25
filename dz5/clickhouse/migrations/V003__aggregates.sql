CREATE TABLE IF NOT EXISTS analytics.agg_daily_metrics
(
    metric_date Date,
    metric_name LowCardinality(String),
    value       Nullable(Float64),
    computed_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (metric_date, metric_name);

CREATE TABLE IF NOT EXISTS analytics.agg_movie_daily
(
    metric_date    Date,
    movie_id       String,
    views          UInt64,
    unique_viewers UInt64,
    finished       UInt64,
    likes          UInt64,
    rank           UInt32,
    computed_at    DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (metric_date, movie_id);

CREATE TABLE IF NOT EXISTS analytics.agg_device_daily
(
    metric_date Date,
    device_type Enum8('MOBILE' = 1, 'DESKTOP' = 2, 'TV' = 3, 'TABLET' = 4),
    users       UInt64,
    views       UInt64,
    computed_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (metric_date, device_type);

CREATE TABLE IF NOT EXISTS analytics.agg_retention_cohort
(
    cohort_date    Date,
    day_offset     UInt8,
    cohort_size    UInt64,
    retained_users UInt64,
    retention_rate Nullable(Float64),
    computed_at    DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (cohort_date, day_offset);
