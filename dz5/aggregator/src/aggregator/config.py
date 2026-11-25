import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    clickhouse_host: str
    clickhouse_port: int
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_database: str
    database_url: str
    interval_seconds: float
    top_movies_limit: int
    retry_attempts: int
    retry_base_seconds: float


def load_settings() -> Settings:
    return Settings(
        clickhouse_host=os.environ["CLICKHOUSE_HOST"],
        clickhouse_port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        clickhouse_user=os.environ["CLICKHOUSE_USER"],
        clickhouse_password=os.environ["CLICKHOUSE_PASSWORD"],
        clickhouse_database=os.getenv("CLICKHOUSE_DATABASE", "analytics"),
        database_url=os.environ["DATABASE_URL"],
        interval_seconds=float(os.getenv("AGGREGATION_INTERVAL_SECONDS", "60")),
        top_movies_limit=int(os.getenv("TOP_MOVIES_LIMIT", "10")),
        retry_attempts=int(os.getenv("RETRY_ATTEMPTS", "5")),
        retry_base_seconds=float(os.getenv("RETRY_BASE_DELAY_MS", "500")) / 1000,
    )
