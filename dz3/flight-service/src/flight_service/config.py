import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_key: str
    grpc_port: int
    redis_sentinels: list[tuple[str, int]]
    redis_master_name: str
    redis_timeout_seconds: float
    cache_ttl_seconds: int
    db_pool_max_size: int


def _parse_hosts(value: str) -> list[tuple[str, int]]:
    hosts = []
    for item in value.split(","):
        host, _, port = item.strip().rpartition(":")
        hosts.append((host, int(port)))
    return hosts


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        api_key=os.environ["FLIGHT_SERVICE_API_KEY"],
        grpc_port=int(os.getenv("GRPC_PORT", "50051")),
        redis_sentinels=_parse_hosts(os.environ["REDIS_SENTINELS"]),
        redis_master_name=os.getenv("REDIS_MASTER_NAME", "mymaster"),
        redis_timeout_seconds=float(os.getenv("REDIS_TIMEOUT_SECONDS", "0.5")),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "300")),
        db_pool_max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
    )
