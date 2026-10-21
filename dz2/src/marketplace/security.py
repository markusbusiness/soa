import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Request

from marketplace.errors import ApiError
from marketplace.runtime import runtime
from marketplace_api.models.error_code import ErrorCode
from marketplace_api.models.extra_models import TokenModel

JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("timing-equalizer")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        await asyncio.to_thread(_hasher.verify, password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return password_hash is not None


@dataclass(frozen=True)
class IssuedToken:
    token: str
    jti: uuid.UUID
    expires_at: datetime


def issue_access_token(user_id: uuid.UUID, role: str) -> IssuedToken:
    return _issue(user_id, "access", runtime.settings.access_token_ttl, {"role": role})


def issue_refresh_token(user_id: uuid.UUID) -> IssuedToken:
    return _issue(user_id, "refresh", runtime.settings.refresh_token_ttl, {})


def _issue(user_id: uuid.UUID, token_type: str, ttl, extra: dict) -> IssuedToken:
    now = datetime.now(timezone.utc)
    jti = uuid.uuid4()
    expires_at = now + ttl
    claims = {"sub": str(user_id), "type": token_type, "jti": str(jti), "iat": now, "exp": expires_at, **extra}
    return IssuedToken(jwt.encode(claims, runtime.settings.jwt_secret, algorithm=JWT_ALGORITHM), jti, expires_at)


def decode_refresh_token(token: str) -> dict:
    try:
        claims = jwt.decode(token, runtime.settings.jwt_secret, algorithms=[JWT_ALGORITHM],
                            options={"require": ["sub", "jti", "exp", "type"]})
    except jwt.PyJWTError:
        raise ApiError(ErrorCode.REFRESH_TOKEN_INVALID, "Refresh token is invalid or expired")
    if claims["type"] != "refresh":
        raise ApiError(ErrorCode.REFRESH_TOKEN_INVALID, "Refresh token is invalid or expired")
    return claims


def _decode_access_token(token: str) -> TokenModel:
    try:
        claims = jwt.decode(token, runtime.settings.jwt_secret, algorithms=[JWT_ALGORITHM],
                            options={"require": ["sub", "role", "exp", "type"]})
    except jwt.ExpiredSignatureError:
        raise ApiError(ErrorCode.TOKEN_EXPIRED, "Access token has expired")
    except jwt.PyJWTError:
        raise ApiError(ErrorCode.TOKEN_INVALID, "Access token is invalid")
    if claims["type"] != "access":
        raise ApiError(ErrorCode.TOKEN_INVALID, "Access token is invalid")
    return TokenModel(sub=claims["sub"], role=claims["role"])


async def authenticate(request: Request) -> TokenModel:
    """Replaces the generated get_token_bearerAuth stub: verifies the JWT and the route's x-allowed-roles."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(ErrorCode.TOKEN_INVALID, "Missing bearer access token")
    principal = _decode_access_token(token.strip())
    request.state.user_id = principal.sub

    route = request.scope.get("route")
    allowed_roles = (getattr(route, "openapi_extra", None) or {}).get("x-allowed-roles", [])
    if principal.role not in allowed_roles:
        raise ApiError(ErrorCode.ACCESS_DENIED, f"Role {principal.role} is not allowed to perform this operation")
    return principal
