CREATE TABLE IF NOT EXISTS analytics.movie_events_queue
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
    search_query     Nullable(String)
)
ENGINE = Kafka
SETTINGS kafka_broker_list = 'kafka-1:9092,kafka-2:9092,kafka-3:9092',
         kafka_topic_list = 'movie-events',
         kafka_group_name = 'clickhouse-movie-events',
         kafka_format = 'AvroConfluent',
         format_avro_schema_registry_url = 'http://schema-registry:8081',
         input_format_avro_allow_missing_fields = 1,
         kafka_handle_error_mode = 'stream',
         kafka_flush_interval_ms = 1000;

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.movie_events_dlq_mv TO analytics.movie_events_dlq AS
SELECT _topic AS kafka_topic, _partition AS kafka_partition, _offset AS kafka_offset,
       _raw_message AS raw_message, _error AS error
FROM analytics.movie_events_queue
WHERE length(_error) > 0;

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.movie_events_mv TO analytics.movie_events AS
SELECT event_id, user_id, movie_id, event_type, timestamp, device_type, session_id, progress_seconds,
       search_query, _partition AS kafka_partition, _offset AS kafka_offset
FROM analytics.movie_events_queue
WHERE length(_error) = 0;
