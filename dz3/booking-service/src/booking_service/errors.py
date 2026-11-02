import logging
import math

import grpc
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from booking_service.grpc_middleware.circuit_breaker import CircuitOpenError

logger = logging.getLogger("booking_service.errors")


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


GRPC_TO_HTTP: dict[grpc.StatusCode, tuple[int, str]] = {
    grpc.StatusCode.INVALID_ARGUMENT: (400, "VALIDATION_ERROR"),
    grpc.StatusCode.NOT_FOUND: (404, "FLIGHT_NOT_FOUND"),
    grpc.StatusCode.RESOURCE_EXHAUSTED: (409, "NOT_ENOUGH_SEATS"),
    grpc.StatusCode.FAILED_PRECONDITION: (409, "FLIGHT_NOT_AVAILABLE"),
    grpc.StatusCode.UNAVAILABLE: (503, "FLIGHT_SERVICE_UNAVAILABLE"),
    grpc.StatusCode.DEADLINE_EXCEEDED: (504, "FLIGHT_SERVICE_TIMEOUT"),
}


def from_grpc(exc: grpc.aio.AioRpcError) -> ApiError:
    status_code, code = GRPC_TO_HTTP.get(exc.code(), (502, "FLIGHT_SERVICE_ERROR"))
    if status_code == 502:
        logger.error("flight service call failed: %s %s", exc.code().name, exc.details())
        return ApiError(status_code, code, "Flight Service returned an unexpected error")
    return ApiError(status_code, code, exc.details() or exc.code().name)


def error_response(status_code: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}},
                        headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.headers)

    @app.exception_handler(grpc.aio.AioRpcError)
    async def handle_grpc_error(_: Request, exc: grpc.aio.AioRpcError) -> JSONResponse:
        error = from_grpc(exc)
        return error_response(error.status_code, error.code, error.message)

    @app.exception_handler(CircuitOpenError)
    async def handle_circuit_open(_: Request, exc: CircuitOpenError) -> JSONResponse:
        return error_response(503, "FLIGHT_SERVICE_CIRCUIT_OPEN",
                              "Flight Service is temporarily unavailable, try again later",
                              {"Retry-After": str(math.ceil(exc.retry_after_seconds))})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'] if p not in ('body', 'query', 'path'))}: {err['msg']}"
            for err in exc.errors()
        )
        return error_response(400, "VALIDATION_ERROR", problems or "Request validation failed")

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(exc.status_code, "HTTP_ERROR", str(exc.detail))
