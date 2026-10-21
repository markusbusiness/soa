import uuid
from datetime import datetime, timedelta, timezone

import jwt

from conftest import assert_error, auth
from marketplace.runtime import runtime


async def test_register_returns_user_without_password(client, api):
    local = uuid.uuid4().hex[:10]
    response = await client.post("/auth/register", json={"email": f"New.{local}@Test.Local", "password": "password123"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == f"new.{local}@test.local"
    assert body["role"] == "USER"
    assert "password" not in body and "password_hash" not in body


async def test_register_duplicate_email(client, api):
    user = await api.user()
    response = await client.post("/auth/register", json={"email": user["email"].upper(), "password": "password123"})
    assert_error(response, 409, "USER_ALREADY_EXISTS")


async def test_register_cannot_self_assign_admin(client):
    response = await client.post("/auth/register", json={"email": "x@test.local", "password": "password123", "role": "ADMIN"})
    body = assert_error(response, 400, "VALIDATION_ERROR")
    assert body["details"]["fields"][0]["field"] == "role"


async def test_login_with_wrong_password(client, api):
    user = await api.user()
    response = await client.post("/auth/login", json={"email": user["email"], "password": "wrong-password"})
    assert_error(response, 401, "INVALID_CREDENTIALS")


async def test_access_token_carries_role_and_short_ttl(api):
    seller = await api.user("SELLER")
    claims = jwt.decode(seller["access_token"], options={"verify_signature": False})
    assert claims["role"] == "SELLER" and claims["type"] == "access"
    assert timedelta(minutes=15) <= timedelta(seconds=claims["exp"] - claims["iat"]) <= timedelta(minutes=30)
    refresh_claims = jwt.decode(seller["refresh_token"], options={"verify_signature": False})
    assert timedelta(days=7) <= timedelta(seconds=refresh_claims["exp"] - refresh_claims["iat"]) <= timedelta(days=30)


async def test_protected_endpoint_without_token(client):
    assert_error(await client.get("/products"), 401, "TOKEN_INVALID")
    assert_error(await client.get("/products", headers={"Authorization": "Bearer garbage"}), 401, "TOKEN_INVALID")


async def test_expired_access_token(client, api):
    user = await api.user()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token = jwt.encode({"sub": user["id"], "role": "USER", "type": "access", "iat": past, "exp": past + timedelta(minutes=15)},
                       runtime.settings.jwt_secret, algorithm="HS256")
    assert_error(await client.get("/products", headers=auth(token)), 401, "TOKEN_EXPIRED")


async def test_refresh_token_is_not_an_access_token(client, api):
    user = await api.user()
    assert_error(await client.get("/products", headers=auth(user["refresh_token"])), 401, "TOKEN_INVALID")


async def test_refresh_rotates_and_rejects_reuse(client, api):
    user = await api.user()
    first = await client.post("/auth/refresh", json={"refresh_token": user["refresh_token"]})
    assert first.status_code == 200, first.text
    rotated = first.json()
    assert (await client.get("/products", headers=auth(rotated["access_token"]))).status_code == 200

    reuse = await client.post("/auth/refresh", json={"refresh_token": user["refresh_token"]})
    assert_error(reuse, 401, "REFRESH_TOKEN_INVALID")
    after_reuse = await client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert_error(after_reuse, 401, "REFRESH_TOKEN_INVALID")


async def test_invalid_refresh_tokens(client, api):
    user = await api.user()
    assert_error(await client.post("/auth/refresh", json={"refresh_token": "garbage"}), 401, "REFRESH_TOKEN_INVALID")
    assert_error(await client.post("/auth/refresh", json={"refresh_token": user["access_token"]}), 401,
                 "REFRESH_TOKEN_INVALID")
