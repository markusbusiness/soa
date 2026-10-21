from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI

import marketplace.handlers  # noqa: F401
from marketplace.config import load_settings
from marketplace.errors import install_error_handlers
from marketplace.request_logging import RequestLoggingMiddleware, configure_logging
from marketplace.runtime import runtime
from marketplace.security import authenticate
from marketplace.services.users import ensure_admin
from marketplace_api.apis.auth_api import router as auth_router
from marketplace_api.apis.orders_api import router as orders_router
from marketplace_api.apis.products_api import router as products_router
from marketplace_api.apis.promo_codes_api import router as promo_codes_router
from marketplace_api.security_api import get_token_bearerAuth


def create_app() -> FastAPI:
    configure_logging()
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await runtime.start(settings)
        await ensure_admin()
        yield
        await runtime.stop()

    app = FastAPI(title="Marketplace API", lifespan=lifespan)
    for router in (auth_router, products_router, orders_router, promo_codes_router):
        app.include_router(router)
    app.dependency_overrides[get_token_bearerAuth] = authenticate
    install_error_handlers(app)
    app.add_middleware(RequestLoggingMiddleware)

    with open(settings.openapi_spec_path, encoding="utf-8") as spec:
        contract = yaml.safe_load(spec)
    app.openapi = lambda: contract

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
