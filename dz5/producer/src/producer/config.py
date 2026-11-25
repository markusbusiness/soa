import os
from dataclasses import dataclass


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class PublisherSettings:
    bootstrap_servers: str
    schema_registry_url: str
    topic: str
    subject: str
    delivery_timeout_ms: int
    max_attempts: int
    backoff_base_seconds: float
    backoff_max_seconds: float


@dataclass(frozen=True)
class GeneratorSettings:
    enabled: bool
    new_users_per_day: int
    movies: int
    backfill_days: int
    seed: str
    state_dir: str


@dataclass(frozen=True)
class Settings:
    publisher: PublisherSettings
    generator: GeneratorSettings


def load_settings() -> Settings:
    topic = os.getenv("KAFKA_TOPIC", "movie-events")
    return Settings(
        publisher=PublisherSettings(
            bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"],
            schema_registry_url=os.environ["SCHEMA_REGISTRY_URL"],
            topic=topic,
            subject=os.getenv("SCHEMA_SUBJECT", f"{topic}-value"),
            delivery_timeout_ms=int(os.getenv("KAFKA_DELIVERY_TIMEOUT_MS", "10000")),
            max_attempts=int(os.getenv("PUBLISH_MAX_ATTEMPTS", "4")),
            backoff_base_seconds=float(os.getenv("PUBLISH_BACKOFF_BASE_MS", "200")) / 1000,
            backoff_max_seconds=float(os.getenv("PUBLISH_BACKOFF_MAX_MS", "5000")) / 1000,
        ),
        generator=GeneratorSettings(
            enabled=_bool("GENERATOR_ENABLED", "false"),
            new_users_per_day=int(os.getenv("GENERATOR_NEW_USERS_PER_DAY", "80")),
            movies=int(os.getenv("GENERATOR_MOVIES", "60")),
            backfill_days=int(os.getenv("GENERATOR_BACKFILL_DAYS", "14")),
            seed=os.getenv("GENERATOR_SEED", "dz5"),
            state_dir=os.getenv("GENERATOR_STATE_DIR", "/state"),
        ),
    )
