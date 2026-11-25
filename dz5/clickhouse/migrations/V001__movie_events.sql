CREATE TABLE IF NOT EXISTS analytics.movie_events
(
    event_id         UUID,
    user_id          String,
    movie_id         String,
    event_type       Enum8('VIEW_STARTED' = 1, 'VIEW_FINISHED' = 2, 'VIEW_PAUSED' = 3,
                           'VIEW_RESUMED' = 4, 'LIKED' = 5, 'SEARCHED' = 6),
    timestamp        DateTime64(3, 'UTC'),
    device_type      Enum8('MOBILE' = 1, 'DESKTOP' = 2, 'TV' = 3, 'TABLET' = 4),
    session_id       String,
    progress_seconds Nullable(Int32),
    search_query     Nullable(String),
    event_date       Date MATERIALIZED toDate(timestamp),
    kafka_partition  UInt16,
    kafka_offset     UInt64,
    ingested_at      DateTime64(3, 'UTC') DEFAULT now64(3),
    INDEX idx_ingested_at ingested_at TYPE minmax GRANULARITY 1
)
ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (event_date, event_type, user_id, event_id);

CREATE TABLE IF NOT EXISTS analytics.movie_events_dlq
(
    received_at     DateTime64(3, 'UTC') DEFAULT now64(3),
    kafka_topic     String,
    kafka_partition UInt16,
    kafka_offset    UInt64,
    raw_message     String,
    error           String
)
ENGINE = MergeTree
ORDER BY received_at
TTL toDateTime(received_at) + INTERVAL 30 DAY;

CREATE TABLE IF NOT EXISTS analytics.movie_events_dlq_resolution
(
    kafka_topic     String,
    kafka_partition UInt16,
    kafka_offset    UInt64,
    outcome         Enum8('recovered' = 1, 'invalid' = 2),
    detail          String,
    resolved_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(resolved_at)
ORDER BY (kafka_topic, kafka_partition, kafka_offset);
