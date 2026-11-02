import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrySettings:
    max_attempts: int
    base_delay_seconds: float
    multiplier: float


@dataclass(frozen=True)
class CircuitBreakerSettings:
    failure_threshold: int
    window_seconds: float
    open_timeout_seconds: float


@dataclass(frozen=True)
class Settings:
    database_url: str
    flight_service_address: str
    flight_service_api_key: str
    grpc_timeout_seconds: float
    retry: RetrySettings
    circuit_breaker: CircuitBreakerSettings
    pending_reservation_timeout_seconds: float
    sweeper_interval_seconds: float
    db_pool_max_size: int


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        flight_service_address=os.environ["FLIGHT_SERVICE_ADDRESS"],
        flight_service_api_key=os.environ["FLIGHT_SERVICE_API_KEY"],
        grpc_timeout_seconds=float(os.getenv("GRPC_TIMEOUT_SECONDS", "2")),
        retry=RetrySettings(
            max_attempts=int(os.getenv("RETRY_MAX_ATTEMPTS", "3")),
            base_delay_seconds=float(os.getenv("RETRY_BASE_DELAY_MS", "100")) / 1000,
            multiplier=float(os.getenv("RETRY_BACKOFF_MULTIPLIER", "2")),
        ),
        circuit_breaker=CircuitBreakerSettings(
            failure_threshold=int(os.getenv("CB_FAILURE_THRESHOLD", "5")),
            window_seconds=float(os.getenv("CB_WINDOW_SECONDS", "30")),
            open_timeout_seconds=float(os.getenv("CB_OPEN_TIMEOUT_SECONDS", "15")),
        ),
        pending_reservation_timeout_seconds=float(os.getenv("PENDING_RESERVATION_TIMEOUT_SECONDS", "60")),
        sweeper_interval_seconds=float(os.getenv("SWEEPER_INTERVAL_SECONDS", "10")),
        db_pool_max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
    )
