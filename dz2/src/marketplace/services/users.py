import logging
import uuid

import asyncpg

from marketplace.errors import ApiError
from marketplace.runtime import runtime
from marketplace.security import (
    decode_refresh_token,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    verify_password,
)
from marketplace_api.models.error_code import ErrorCode

logger = logging.getLogger("marketplace.users")


async def register(email: str, password: str, role: str) -> asyncpg.Record:
    password_hash = await hash_password(password)
    try:
        return await runtime.pool.fetchrow(
            """
            INSERT INTO users (email, password_hash, role) VALUES ($1, $2, $3)
            RETURNING id, email, role, created_at
            """,
            email.lower(), password_hash, role,
        )
    except asyncpg.UniqueViolationError:
        raise ApiError(ErrorCode.USER_ALREADY_EXISTS, "User with this email already exists")


async def login(email: str, password: str) -> dict:
    user = await runtime.pool.fetchrow("SELECT id, role, password_hash FROM users WHERE email = $1", email.lower())
    if not await verify_password(user["password_hash"] if user else None, password):
        raise ApiError(ErrorCode.INVALID_CREDENTIALS, "Invalid email or password")
    return await _issue_pair(runtime.pool, user["id"], user["role"])


async def refresh(refresh_token: str) -> dict:
    claims = decode_refresh_token(refresh_token)
    invalid = ApiError(ErrorCode.REFRESH_TOKEN_INVALID, "Refresh token is invalid or expired")
    async with runtime.transaction() as conn:
        stored = await conn.fetchrow(
            "SELECT user_id, revoked_at, expires_at > now() AS alive FROM refresh_tokens WHERE id = $1 FOR UPDATE",
            uuid.UUID(claims["jti"]),
        )
        if stored is None or str(stored["user_id"]) != claims["sub"] or not stored["alive"]:
            raise invalid
        if stored["revoked_at"] is None:
            await conn.execute("UPDATE refresh_tokens SET revoked_at = now() WHERE id = $1", uuid.UUID(claims["jti"]))
            role = await conn.fetchval("SELECT role FROM users WHERE id = $1", stored["user_id"])
            return await _issue_pair(conn, stored["user_id"], role)
        await conn.execute(
            "UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
            stored["user_id"],
        )
    logger.warning("revoked refresh token reused, all sessions of user %s revoked", stored["user_id"])
    raise invalid


async def _issue_pair(conn, user_id: uuid.UUID, role: str) -> dict:
    access = issue_access_token(user_id, role)
    refresh_token = issue_refresh_token(user_id)
    await conn.execute(
        "INSERT INTO refresh_tokens (id, user_id, expires_at) VALUES ($1, $2, $3)",
        refresh_token.jti, user_id, refresh_token.expires_at,
    )
    return {
        "access_token": access.token,
        "refresh_token": refresh_token.token,
        "token_type": "Bearer",
        "expires_in": int(runtime.settings.access_token_ttl.total_seconds()),
        "refresh_expires_in": int(runtime.settings.refresh_token_ttl.total_seconds()),
    }


async def ensure_admin() -> None:
    settings = runtime.settings
    if not settings.admin_email or not settings.admin_password:
        return
    password_hash = await hash_password(settings.admin_password)
    created = await runtime.pool.fetchval(
        """
        INSERT INTO users (email, password_hash, role) VALUES ($1, $2, 'ADMIN')
        ON CONFLICT (email) DO NOTHING RETURNING id
        """,
        settings.admin_email.lower(), password_hash,
    )
    if created:
        logger.info("bootstrap admin %s created", settings.admin_email)
