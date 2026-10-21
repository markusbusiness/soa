import os
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    access_token_ttl: timedelta
    refresh_token_ttl: timedelta
    order_rate_limit: timedelta
    admin_email: str | None
    admin_password: str | None
    openapi_spec_path: str
    db_pool_max_size: int


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        jwt_secret=os.environ["JWT_SECRET"],
        access_token_ttl=timedelta(minutes=int(os.getenv("ACCESS_TOKEN_TTL_MINUTES", "15"))),
        refresh_token_ttl=timedelta(days=int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "7"))),
        order_rate_limit=timedelta(minutes=float(os.getenv("ORDER_RATE_LIMIT_MINUTES", "1"))),
        admin_email=os.getenv("ADMIN_EMAIL"),
        admin_password=os.getenv("ADMIN_PASSWORD"),
        openapi_spec_path=os.getenv("OPENAPI_SPEC_PATH", "openapi/marketplace.yaml"),
        db_pool_max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
    )
