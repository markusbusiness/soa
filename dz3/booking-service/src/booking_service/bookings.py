import logging
import uuid

import asyncpg
import grpc

from booking_service.errors import ApiError, from_grpc
from booking_service.flight_client import FlightClient
from booking_service.grpc_middleware.circuit_breaker import CircuitOpenError
from booking_service.schemas import CreateBookingRequest
from flight.v1 import flight_pb2

logger = logging.getLogger("booking_service.bookings")

# The reservation may have been applied before the error was observed, so it must be compensated.
AMBIGUOUS_CODES = frozenset({
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.CANCELLED,
    grpc.StatusCode.UNKNOWN,
    grpc.StatusCode.INTERNAL,
})


class BookingService:
    def __init__(self, pool: asyncpg.Pool, flights: FlightClient):
        self._pool = pool
        self._flights = flights

    async def create(self, request: CreateBookingRequest) -> asyncpg.Record:
        booking_id = uuid.uuid4()
        flight_id = str(request.flight_id)

        try:
            flight = await self._flights.get_flight(flight_id)
        except grpc.aio.AioRpcError as exc:
            raise from_grpc(exc) from exc
        if flight.status != flight_pb2.FLIGHT_STATUS_SCHEDULED:
            raise ApiError(409, "FLIGHT_NOT_AVAILABLE", f"flight {flight_id} is not open for booking")

        await self._pool.execute("INSERT INTO pending_reservations (booking_id, flight_id) VALUES ($1, $2)",
                                 booking_id, request.flight_id)
        try:
            await self._flights.reserve_seats(flight_id, request.seat_count, str(booking_id))
        except grpc.aio.AioRpcError as exc:
            if exc.code() not in AMBIGUOUS_CODES:
                await self._drop_pending(booking_id)
            else:
                logger.warning("reservation outcome for booking %s unknown (%s), left to the sweeper",
                               booking_id, exc.code().name)
            raise from_grpc(exc) from exc
        except CircuitOpenError:
            await self._drop_pending(booking_id)
            raise

        total_price_minor = request.seat_count * flight.price.amount_minor
        async with self._pool.acquire() as conn, conn.transaction():
            if await conn.execute("DELETE FROM pending_reservations WHERE booking_id = $1", booking_id) == "DELETE 0":
                raise ApiError(503, "BOOKING_EXPIRED", "reservation expired before the booking was saved")
            booking = await conn.fetchrow(
                """
                INSERT INTO bookings (id, user_id, flight_id, passenger_name, passenger_email, seat_count,
                                      total_price_minor, currency_code)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING *
                """,
                booking_id, request.user_id, request.flight_id, request.passenger_name, request.passenger_email,
                request.seat_count, total_price_minor, flight.price.currency_code,
            )
        logger.info("booking %s confirmed: %d seats on flight %s", booking_id, request.seat_count, flight_id)
        return booking

    async def get(self, booking_id: uuid.UUID) -> asyncpg.Record:
        booking = await self._pool.fetchrow("SELECT * FROM bookings WHERE id = $1", booking_id)
        if booking is None:
            raise ApiError(404, "BOOKING_NOT_FOUND", f"booking {booking_id} not found")
        return booking

    async def list_for_user(self, user_id: str) -> list[asyncpg.Record]:
        return await self._pool.fetch("SELECT * FROM bookings WHERE user_id = $1 ORDER BY created_at DESC, id",
                                      user_id)

    async def cancel(self, booking_id: uuid.UUID) -> asyncpg.Record:
        async with self._pool.acquire() as conn, conn.transaction():
            booking = await conn.fetchrow("SELECT * FROM bookings WHERE id = $1 FOR UPDATE", booking_id)
            if booking is None:
                raise ApiError(404, "BOOKING_NOT_FOUND", f"booking {booking_id} not found")
            if booking["status"] != "CONFIRMED":
                raise ApiError(409, "INVALID_BOOKING_STATUS", f"booking {booking_id} is {booking['status']}")
            try:
                await self._flights.release_reservation(str(booking_id))
            except grpc.aio.AioRpcError as exc:
                if exc.code() != grpc.StatusCode.NOT_FOUND:
                    raise from_grpc(exc) from exc
                logger.error("booking %s has no reservation in Flight Service, cancelling anyway", booking_id)
            booking = await conn.fetchrow(
                "UPDATE bookings SET status = 'CANCELLED', updated_at = now() WHERE id = $1 RETURNING *",
                booking_id,
            )
        logger.info("booking %s cancelled", booking_id)
        return booking

    async def _drop_pending(self, booking_id: uuid.UUID) -> None:
        await self._pool.execute("DELETE FROM pending_reservations WHERE booking_id = $1", booking_id)
