import enum
import logging
import time
from collections import deque
from typing import Callable

import grpc

from booking_service.config import CircuitBreakerSettings

logger = logging.getLogger("booking_service.circuit_breaker")

# Codes that signal a broken dependency. Business errors (NOT_FOUND, RESOURCE_EXHAUSTED, ...) are successful calls.
FAILURE_CODES = frozenset({
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.INTERNAL,
    grpc.StatusCode.UNKNOWN,
    grpc.StatusCode.DATA_LOSS,
})


class State(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    def __init__(self, retry_after_seconds: float):
        super().__init__("circuit breaker is open")
        self.retry_after_seconds = retry_after_seconds


class CircuitBreaker:
    def __init__(self, settings: CircuitBreakerSettings, clock: Callable[[], float] = time.monotonic):
        self._settings = settings
        self._clock = clock
        self._state = State.CLOSED
        self._failures: deque[float] = deque()
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> State:
        return self._state

    def acquire(self) -> None:
        if self._state is State.OPEN:
            remaining = self._opened_at + self._settings.open_timeout_seconds - self._clock()
            if remaining > 0:
                raise CircuitOpenError(remaining)
            self._transition(State.HALF_OPEN)
        if self._state is State.HALF_OPEN:
            if self._probe_in_flight:
                raise CircuitOpenError(self._settings.open_timeout_seconds)
            self._probe_in_flight = True

    def on_success(self) -> None:
        if self._state is State.HALF_OPEN:
            self._probe_in_flight = False
            self._transition(State.CLOSED)

    def on_failure(self) -> None:
        now = self._clock()
        if self._state is State.HALF_OPEN:
            self._probe_in_flight = False
            self._open(now)
            return
        if self._state is State.OPEN:
            return
        self._failures.append(now)
        while self._failures and self._failures[0] <= now - self._settings.window_seconds:
            self._failures.popleft()
        if len(self._failures) >= self._settings.failure_threshold:
            self._open(now)

    def _open(self, now: float) -> None:
        self._opened_at = now
        self._transition(State.OPEN)

    def _transition(self, new_state: State) -> None:
        logger.warning("circuit breaker %s -> %s", self._state.value, new_state.value)
        self._state = new_state
        self._failures.clear()


class CircuitBreakerInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    def __init__(self, breaker: CircuitBreaker):
        self._breaker = breaker

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        self._breaker.acquire()
        try:
            response = await (await continuation(client_call_details, request))
        except grpc.aio.AioRpcError as exc:
            if exc.code() in FAILURE_CODES:
                self._breaker.on_failure()
            else:
                self._breaker.on_success()
            raise
        except BaseException:
            self._breaker.on_failure()
            raise
        self._breaker.on_success()
        return response
