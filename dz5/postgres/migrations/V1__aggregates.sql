CREATE TABLE daily_metrics (
    metric_date date             NOT NULL,
    metric_name text             NOT NULL,
    value       double precision,
    computed_at timestamptz      NOT NULL,
    updated_at  timestamptz      NOT NULL,
    PRIMARY KEY (metric_date, metric_name)
);

CREATE TABLE top_movies (
    metric_date    date        NOT NULL,
    rank           integer     NOT NULL CHECK (rank > 0),
    movie_id       text        NOT NULL,
    views          bigint      NOT NULL CHECK (views >= 0),
    unique_viewers bigint      NOT NULL CHECK (unique_viewers >= 0),
    finished       bigint      NOT NULL CHECK (finished >= 0),
    likes          bigint      NOT NULL CHECK (likes >= 0),
    computed_at    timestamptz NOT NULL,
    updated_at     timestamptz NOT NULL,
    PRIMARY KEY (metric_date, rank)
);

CREATE TABLE device_daily (
    metric_date date        NOT NULL,
    device_type text        NOT NULL CHECK (device_type IN ('MOBILE', 'DESKTOP', 'TV', 'TABLET')),
    users       bigint      NOT NULL CHECK (users >= 0),
    views       bigint      NOT NULL CHECK (views >= 0),
    computed_at timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL,
    PRIMARY KEY (metric_date, device_type)
);

CREATE TABLE retention_cohorts (
    cohort_date    date             NOT NULL,
    day_offset     smallint         NOT NULL CHECK (day_offset BETWEEN 0 AND 7),
    cohort_size    bigint           NOT NULL CHECK (cohort_size >= 0),
    retained_users bigint           NOT NULL CHECK (retained_users >= 0),
    retention_rate double precision CHECK (retention_rate BETWEEN 0 AND 1),
    computed_at    timestamptz      NOT NULL,
    updated_at     timestamptz      NOT NULL,
    PRIMARY KEY (cohort_date, day_offset)
);

CREATE TABLE aggregate_dates (
    metric_date date        PRIMARY KEY,
    computed_at timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL
);

CREATE TABLE aggregation_state (
    id                 boolean     PRIMARY KEY DEFAULT true CHECK (id),
    ingested_watermark timestamptz NOT NULL,
    updated_at         timestamptz NOT NULL
);

CREATE TABLE s3_exports (
    metric_date       date        PRIMARY KEY REFERENCES aggregate_dates (metric_date),
    object_key        text        NOT NULL,
    source_updated_at timestamptz NOT NULL,
    exported_at       timestamptz NOT NULL
);
