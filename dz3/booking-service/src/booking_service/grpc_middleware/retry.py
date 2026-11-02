import asyncio
import logging

import grpc

from booking_service.config import RetrySettings

logger = logging.getLogger("booking_service.retry")

RETRYABLE_CODES = frozenset({grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED})


class RetryInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    """Safe for every Flight Service method: ReserveSeats and ReleaseReservation are idempotent by booking_id."""

    def __init__(self, settings: RetrySettings):
        self._settings = settings

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        method = client_call_details.method.decode() if isinstance(client_call_details.method, bytes) \
            else client_call_details.method
        delay = self._settings.base_delay_seconds
        for attempt in range(1, self._settings.max_attempts + 1):
            try:
                return await (await continuation(client_call_details, request))
            except grpc.aio.AioRpcError as exc:
                if exc.code() not in RETRYABLE_CODES or attempt == self._settings.max_attempts:
                    if exc.code() in RETRYABLE_CODES:
                        logger.error("%s failed after %d attempts: %s", method, attempt, exc.code().name)
                    raise
                logger.warning("%s attempt %d/%d failed with %s, retrying in %d ms", method, attempt,
                               self._settings.max_attempts, exc.code().name, round(delay * 1000))
            await asyncio.sleep(delay)
            delay *= self._settings.multiplier
