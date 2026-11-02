import asyncio
import logging

import asyncpg
import grpc

from booking_service.flight_client import FlightClient
from booking_service.grpc_middleware.circuit_breaker import CircuitOpenError

logger = logging.getLogger("booking_service.sweeper")


class PendingReservationSweeper:
    """Releases reservations whose booking was never committed (failed or interrupted POST /bookings)."""

    def __init__(self, pool: asyncpg.Pool, flights: FlightClient, timeout_seconds: float, interval_seconds: float):
        self._pool = pool
        self._flights = flights
        self._timeout = timeout_seconds
        self._interval = interval_seconds

    async def run(self) -> None:
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sweep failed")
            await asyncio.sleep(self._interval)

    async def sweep(self) -> None:
        stale = await self._pool.fetch(
            """
            SELECT booking_id FROM pending_reservations
            WHERE created_at < now() - make_interval(secs => $1)
            ORDER BY created_at LIMIT 100
            """,
            self._timeout,
        )
        for row in stale:
            try:
                await self._release(row["booking_id"])
            except CircuitOpenError:
                return
            except grpc.aio.AioRpcError as exc:
                logger.warning("cannot release reservation of booking %s yet: %s", row["booking_id"], exc.code().name)
                if exc.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                    return

    async def _release(self, booking_id) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchval(
                "SELECT booking_id FROM pending_reservations WHERE booking_id = $1 FOR UPDATE SKIP LOCKED", booking_id
            )
            if locked is None:
                return
            try:
                await self._flights.release_reservation(str(booking_id))
                logger.info("released orphaned reservation of booking %s", booking_id)
            except grpc.aio.AioRpcError as exc:
                if exc.code() != grpc.StatusCode.NOT_FOUND:
                    raise
            await conn.execute("DELETE FROM pending_reservations WHERE booking_id = $1", booking_id)
