import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from marketplace.errors import error_response
from marketplace_api.models.error_code import ErrorCode

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SENSITIVE_KEY_PARTS = ("password", "token", "secret")
MAX_LOGGED_BODY = 10_000

access_logger = logging.getLogger("marketplace.access")
error_logger = logging.getLogger("marketplace.errors")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "payload", None)
        if payload is None:
            payload = {
                "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if any(part in key.lower() for part in SENSITIVE_KEY_PARTS) else mask_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    return value


def _loggable_body(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return mask_sensitive(json.loads(raw))
    except ValueError:
        return raw[:MAX_LOGGED_BODY].decode("utf-8", errors="replace")


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        method = scope["method"]
        body = bytearray()
        status_code = 500
        response_started = False

        async def receive_and_capture() -> Message:
            message = await receive()
            if method in MUTATING_METHODS and message["type"] == "http.request" and len(body) < MAX_LOGGED_BODY:
                body.extend(message.get("body", b""))
            return message

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive_and_capture, send_with_request_id)
        except Exception:
            error_logger.exception("unhandled error, request_id=%s", request_id)
            if response_started:
                raise
            response = error_response(500, ErrorCode.INTERNAL_ERROR, "Internal server error")
            await response(scope, receive, send_with_request_id)
        finally:
            entry = {
                "request_id": request_id,
                "method": method,
                "endpoint": scope["path"],
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "user_id": state.get("user_id"),
                "timestamp": started_at.isoformat(),
            }
            if method in MUTATING_METHODS:
                entry["request_body"] = _loggable_body(bytes(body))
            access_logger.info("request", extra={"payload": entry})
