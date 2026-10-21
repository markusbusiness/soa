import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from marketplace_api.models.error_code import ErrorCode
from marketplace_api.models.error_response import ErrorResponse

logger = logging.getLogger("marketplace.errors")

HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.TOKEN_INVALID: 401,
    ErrorCode.TOKEN_EXPIRED: 401,
    ErrorCode.REFRESH_TOKEN_INVALID: 401,
    ErrorCode.INVALID_CREDENTIALS: 401,
    ErrorCode.ACCESS_DENIED: 403,
    ErrorCode.ORDER_OWNERSHIP_VIOLATION: 403,
    ErrorCode.PRODUCT_NOT_FOUND: 404,
    ErrorCode.ORDER_NOT_FOUND: 404,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.USER_ALREADY_EXISTS: 409,
    ErrorCode.PRODUCT_INACTIVE: 409,
    ErrorCode.ORDER_HAS_ACTIVE: 409,
    ErrorCode.INVALID_STATE_TRANSITION: 409,
    ErrorCode.INSUFFICIENT_STOCK: 409,
    ErrorCode.PROMO_CODE_ALREADY_EXISTS: 409,
    ErrorCode.PROMO_CODE_INVALID: 422,
    ErrorCode.PROMO_CODE_MIN_AMOUNT: 422,
    ErrorCode.ORDER_LIMIT_EXCEEDED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
}


class ApiError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    @property
    def status_code(self) -> int:
        return HTTP_STATUS[self.code]


def validation_error(field: str, message: str) -> ApiError:
    return ApiError(
        ErrorCode.VALIDATION_ERROR,
        "Request validation failed",
        {"fields": [{"field": field, "message": message}]},
    )


def error_response(status_code: int, code: ErrorCode, message: str,
                   details: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    body = ErrorResponse(error_code=code, message=message, details=details)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"), headers=headers)


# Drops pydantic union-branch segments such as "constrained-float" from the location.
def _field_path(loc: tuple) -> str:
    parts = []
    for i, part in enumerate(loc):
        if i == 0 and part in ("body", "query", "path", "header"):
            continue
        if isinstance(part, str) and (part in ("int", "float", "str") or "[" in part or part.startswith("constrained-")):
            continue
        parts.append(f"[{part}]" if isinstance(part, int) else part)
    path = ".".join(str(p) for p in parts).replace(".[", "[")
    if path:
        return path
    return str(loc[0]) if loc else "body"


def _validation_fields(exc: RequestValidationError) -> list[dict[str, str]]:
    fields: dict[str, str] = {}
    for error in exc.errors():
        if error.get("type") == "json_invalid":
            fields.setdefault("body", "Malformed JSON")
            continue
        if error.get("type") == "missing" and tuple(error.get("loc", ())) == ("body",):
            fields.setdefault("body", "Request body is required")
            continue
        fields.setdefault(_field_path(tuple(error.get("loc", ()))), error.get("msg", "Invalid value"))
    return [{"field": field, "message": message} for field, message in fields.items()]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        headers = None
        if exc.code == ErrorCode.ORDER_LIMIT_EXCEEDED and exc.details:
            headers = {"Retry-After": str(exc.details.get("retry_after_seconds", 0))}
        return error_response(exc.status_code, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(400, ErrorCode.VALIDATION_ERROR, "Request validation failed",
                              {"fields": _validation_fields(exc)})

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(404, ErrorCode.NOT_FOUND, "Resource not found")
        if exc.status_code == 405:
            return error_response(405, ErrorCode.METHOD_NOT_ALLOWED, "Method not allowed")
        return error_response(500, ErrorCode.INTERNAL_ERROR, "Internal server error")

    @app.exception_handler(ResponseValidationError)
    async def handle_response_validation_error(_: Request, exc: ResponseValidationError) -> JSONResponse:
        logger.error("response does not match the contract: %s", exc.errors())
        return error_response(500, ErrorCode.INTERNAL_ERROR, "Internal server error")
