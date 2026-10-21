import os
import uuid
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

os.environ.setdefault("ORDER_RATE_LIMIT_MINUTES", "0")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.local")
os.environ.setdefault("ADMIN_PASSWORD", "admin-password")

from marketplace.main import create_app  # noqa: E402
from marketplace.runtime import runtime  # noqa: E402


@pytest.fixture(scope="session")
async def app():
    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture(scope="session")
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.fixture
def rate_limit():
    original = runtime.settings

    def set_limit(minutes: float) -> None:
        runtime.settings = replace(original, order_rate_limit=timedelta(minutes=minutes))

    yield set_limit
    runtime.settings = original


class Api:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def login(self, email: str, password: str) -> dict:
        response = await self.client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return response.json()

    async def user(self, role: str = "USER") -> dict:
        email = f"{role.lower()}-{uuid.uuid4().hex[:12]}@test.local"
        response = await self.client.post("/auth/register", json={"email": email, "password": "password123", "role": role})
        assert response.status_code == 201, response.text
        tokens = await self.login(email, "password123")
        return {"id": response.json()["id"], "email": email, "headers": auth(tokens["access_token"]), **tokens}

    async def admin(self) -> dict:
        tokens = await self.login(os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"])
        return {"headers": auth(tokens["access_token"]), **tokens}

    async def product(self, seller: dict, **overrides) -> dict:
        body = {"name": "Phone", "description": "Smartphone", "price": 100.0, "stock": 10,
                "category": f"cat-{uuid.uuid4().hex[:8]}", "status": "ACTIVE", **overrides}
        response = await self.client.post("/products", json=body, headers=seller["headers"])
        assert response.status_code == 201, response.text
        return response.json()

    async def promo(self, creator: dict, **overrides) -> dict:
        body = {"code": f"P{uuid.uuid4().hex[:10].upper()}", "discount_type": "PERCENTAGE", "discount_value": 10,
                "min_order_amount": 0, "max_uses": 5, "valid_from": "2020-01-01T00:00:00Z",
                "valid_until": "2099-01-01T00:00:00Z", "active": True, **overrides}
        response = await self.client.post("/promo-codes", json=body, headers=creator["headers"])
        assert response.status_code == 201, response.text
        return response.json()

    async def order(self, buyer: dict, items: list[tuple[dict, int]], promo_code: str | None = None) -> httpx.Response:
        body = {"items": [{"product_id": product["id"], "quantity": quantity} for product, quantity in items]}
        if promo_code is not None:
            body["promo_code"] = promo_code
        return await self.client.post("/orders", json=body, headers=buyer["headers"])


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def api(client) -> Api:
    return Api(client)


def assert_error(response: httpx.Response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error_code"] == code, body
    assert isinstance(body["message"], str) and body["message"]
    return body
