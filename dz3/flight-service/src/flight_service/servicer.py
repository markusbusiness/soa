import logging
import re
import uuid
import asyncpg
import grpc

from flight.v1 import flight_pb2, flight_pb2_grpc
from flight_service.cache import FlightCache, flight_key, route_keys, search_key
from flight_service.convert import (flight_status_name, flight_to_proto, from_timestamp, reservation_to_proto,
                                    utc_date)

logger = logging.getLogger("flight_service.servicer")

IATA_AIRPORT = re.compile(r"^[A-Z]{3}$")
FLIGHT_NUMBER = re.compile(r"^([A-Z0-9]{2})([0-9]{1,4})$")

FLIGHT_SELECT = """
    SELECT f.id, f.airline_code, al.name AS airline_name, f.number,
           f.origin_code, o.name AS origin_name, o.city AS origin_city,
           f.destination_code, d.name AS destination_name, d.city AS destination_city,
           f.departure_time, f.arrival_time, f.total_seats, f.available_seats,
           f.price_minor, f.currency_code, f.status
    FROM flights f
    JOIN airlines al ON al.iata_code = f.airline_code
    JOIN airports o ON o.iata_code = f.origin_code
    JOIN airports d ON d.iata_code = f.destination_code
"""

STATUS_TRANSITIONS = {
    "SCHEDULED": {"DEPARTED", "CANCELLED"},
    "DEPARTED": {"COMPLETED"},
    "CANCELLED": set(),
    "COMPLETED": set(),
}


class FlightServicer(flight_pb2_grpc.FlightServiceServicer):
    def __init__(self, pool: asyncpg.Pool, cache: FlightCache):
        self._pool = pool
        self._cache = cache

    async def SearchFlights(self, request, context):
        origin = await _airport_code(request.origin, "origin", context)
        destination = await _airport_code(request.destination, "destination", context)
        departure_date = utc_date(from_timestamp(request.departure_date)) if request.HasField("departure_date") else None

        key = search_key(origin, destination, departure_date)
        cached = await self._cache.get(key)
        if cached is not None:
            return flight_pb2.SearchFlightsResponse.FromString(cached)

        rows = await self._pool.fetch(
            FLIGHT_SELECT + """
            WHERE f.origin_code = $1 AND f.destination_code = $2 AND f.status = 'SCHEDULED'
              AND ($3::date IS NULL OR (f.departure_time AT TIME ZONE 'UTC')::date = $3)
            ORDER BY f.departure_time, f.id
            """,
            origin, destination, departure_date,
        )
        response = flight_pb2.SearchFlightsResponse(flights=[flight_to_proto(row) for row in rows])
        await self._cache.set(key, response.SerializeToString())
        return response

    async def GetFlight(self, request, context):
        flight_id = await _uuid(request.flight_id, "flight_id", context)
        key = flight_key(str(flight_id))
        cached = await self._cache.get(key)
        if cached is not None:
            return flight_pb2.Flight.FromString(cached)

        row = await self._pool.fetchrow(FLIGHT_SELECT + " WHERE f.id = $1", flight_id)
        if row is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"flight {flight_id} not found")
        flight = flight_to_proto(row)
        await self._cache.set(key, flight.SerializeToString())
        return flight

    async def ReserveSeats(self, request, context):
        flight_id = await _uuid(request.flight_id, "flight_id", context)
        booking_id = await _uuid(request.booking_id, "booking_id", context)
        if request.seat_count <= 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "seat_count must be positive")

        try:
            async with self._pool.acquire() as conn, conn.transaction():
                flight = await conn.fetchrow(
                    """
                    SELECT id, origin_code, destination_code, departure_time, available_seats, status
                    FROM flights WHERE id = $1 FOR UPDATE
                    """,
                    flight_id,
                )
                if flight is None:
                    await context.abort(grpc.StatusCode.NOT_FOUND, f"flight {flight_id} not found")

                existing = await conn.fetchrow("SELECT * FROM seat_reservations WHERE booking_id = $1", booking_id)
                if existing is not None:
                    return await _replayed_reservation(existing, flight_id, request.seat_count, context)

                if flight["status"] != "SCHEDULED":
                    await context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                                        f"flight {flight_id} is {flight['status']}")
                if flight["available_seats"] < request.seat_count:
                    await context.abort(
                        grpc.StatusCode.RESOURCE_EXHAUSTED,
                        f"requested {request.seat_count} seats, {flight['available_seats']} available",
                    )

                await conn.execute(
                    "UPDATE flights SET available_seats = available_seats - $2, updated_at = now() WHERE id = $1",
                    flight_id, request.seat_count,
                )
                reservation = await conn.fetchrow(
                    """
                    INSERT INTO seat_reservations (flight_id, booking_id, seat_count)
                    VALUES ($1, $2, $3) RETURNING *
                    """,
                    flight_id, booking_id, request.seat_count,
                )
        except asyncpg.UniqueViolationError:
            existing = await self._pool.fetchrow("SELECT * FROM seat_reservations WHERE booking_id = $1", booking_id)
            return await _replayed_reservation(existing, flight_id, request.seat_count, context)

        logger.info("reserved %d seats on flight %s for booking %s", request.seat_count, flight_id, booking_id)
        await self._invalidate_flight(flight)
        return reservation_to_proto(reservation)

    async def ReleaseReservation(self, request, context):
        booking_id = await _uuid(request.booking_id, "booking_id", context)
        flight_id = await self._pool.fetchval("SELECT flight_id FROM seat_reservations WHERE booking_id = $1",
                                              booking_id)
        if flight_id is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"no reservation for booking {booking_id}")

        async with self._pool.acquire() as conn, conn.transaction():
            flight = await conn.fetchrow(
                "SELECT id, origin_code, destination_code, departure_time FROM flights WHERE id = $1 FOR UPDATE",
                flight_id,
            )
            reservation = await conn.fetchrow(
                "SELECT * FROM seat_reservations WHERE booking_id = $1 FOR UPDATE", booking_id
            )
            if reservation["status"] == "RELEASED":
                logger.info("reservation for booking %s already released", booking_id)
                return reservation_to_proto(reservation)
            if reservation["status"] != "ACTIVE":
                await context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                                    f"reservation for booking {booking_id} is {reservation['status']}")

            await conn.execute(
                "UPDATE flights SET available_seats = available_seats + $2, updated_at = now() WHERE id = $1",
                flight_id, reservation["seat_count"],
            )
            reservation = await conn.fetchrow(
                """
                UPDATE seat_reservations SET status = 'RELEASED', updated_at = now()
                WHERE booking_id = $1 RETURNING *
                """,
                booking_id,
            )

        logger.info("released %d seats on flight %s for booking %s", reservation["seat_count"], flight_id, booking_id)
        await self._invalidate_flight(flight)
        return reservation_to_proto(reservation)

    async def CreateFlight(self, request, context):
        match = FLIGHT_NUMBER.match(request.flight_number)
        if match is None:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                                "flight_number must be an airline code followed by 1-4 digits, e.g. SU1234")
        origin = await _airport_code(request.origin, "origin", context)
        destination = await _airport_code(request.destination, "destination", context)
        if not request.HasField("departure_time") or not request.HasField("arrival_time"):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "departure_time and arrival_time are required")
        if request.total_seats <= 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "total_seats must be positive")
        await _validate_price(request.price, request.HasField("price"), context)
        departure, arrival = from_timestamp(request.departure_time), from_timestamp(request.arrival_time)
        await _validate_schedule(origin, destination, departure, arrival, context)

        try:
            flight_id = await self._pool.fetchval(
                """
                INSERT INTO flights (airline_code, number, origin_code, destination_code, departure_time,
                                     arrival_time, total_seats, available_seats, price_minor, currency_code)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $7, $8, $9) RETURNING id
                """,
                match.group(1), match.group(2), origin, destination, departure, arrival,
                request.total_seats, request.price.amount_minor, request.price.currency_code,
            )
        except asyncpg.UniqueViolationError:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS,
                                f"flight {request.flight_number} on {utc_date(departure)} already exists")
        except asyncpg.ForeignKeyViolationError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "unknown airline or airport code")

        row = await self._pool.fetchrow(FLIGHT_SELECT + " WHERE f.id = $1", flight_id)
        logger.info("created flight %s %s", flight_id, request.flight_number)
        await self._cache.invalidate(route_keys(origin, destination, utc_date(departure)))
        return flight_to_proto(row)

    async def UpdateFlight(self, request, context):
        flight_id = await _uuid(request.flight_id, "flight_id", context)
        if request.HasField("price"):
            await _validate_price(request.price, True, context)

        try:
            async with self._pool.acquire() as conn, conn.transaction():
                current = await conn.fetchrow("SELECT * FROM flights WHERE id = $1 FOR UPDATE", flight_id)
                if current is None:
                    await context.abort(grpc.StatusCode.NOT_FOUND, f"flight {flight_id} not found")

                status = current["status"]
                if request.HasField("status"):
                    if request.status == flight_pb2.FLIGHT_STATUS_UNSPECIFIED:
                        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "status must be specified")
                    new_status = flight_status_name(request.status)
                    if new_status != status and new_status not in STATUS_TRANSITIONS[status]:
                        await context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                                            f"status transition {status} -> {new_status} is not allowed")
                    status = new_status

                departure = (from_timestamp(request.departure_time) if request.HasField("departure_time")
                             else current["departure_time"])
                arrival = (from_timestamp(request.arrival_time) if request.HasField("arrival_time")
                           else current["arrival_time"])
                await _validate_schedule(current["origin_code"], current["destination_code"], departure, arrival,
                                         context)
                price_minor, currency = current["price_minor"], current["currency_code"]
                if request.HasField("price"):
                    price_minor, currency = request.price.amount_minor, request.price.currency_code

                await conn.execute(
                    """
                    UPDATE flights SET status = $2, departure_time = $3, arrival_time = $4,
                                       price_minor = $5, currency_code = $6, updated_at = now()
                    WHERE id = $1
                    """,
                    flight_id, status, departure, arrival, price_minor, currency,
                )
                row = await conn.fetchrow(FLIGHT_SELECT + " WHERE f.id = $1", flight_id)
        except asyncpg.UniqueViolationError:
            await context.abort(grpc.StatusCode.ALREADY_EXISTS,
                                "another flight with this number already departs on that date")

        logger.info("updated flight %s", flight_id)
        keys = [flight_key(str(flight_id))]
        keys += route_keys(current["origin_code"], current["destination_code"], utc_date(current["departure_time"]))
        keys += route_keys(row["origin_code"], row["destination_code"], utc_date(row["departure_time"]))
        await self._cache.invalidate(sorted(set(keys)))
        return flight_to_proto(row)

    async def _invalidate_flight(self, flight: asyncpg.Record) -> None:
        await self._cache.invalidate(
            [flight_key(str(flight["id"]))]
            + route_keys(flight["origin_code"], flight["destination_code"], utc_date(flight["departure_time"]))
        )


async def _replayed_reservation(existing: asyncpg.Record, flight_id: uuid.UUID, seat_count: int, context):
    if existing["flight_id"] != flight_id or existing["seat_count"] != seat_count:
        await context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                            f"booking {existing['booking_id']} already has a reservation with other parameters")
    if existing["status"] != "ACTIVE":
        await context.abort(grpc.StatusCode.FAILED_PRECONDITION,
                            f"reservation for booking {existing['booking_id']} is {existing['status']}")
    logger.info("idempotent replay of ReserveSeats for booking %s", existing["booking_id"])
    return reservation_to_proto(existing)


async def _uuid(value: str, field: str, context) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"{field} must be a UUID")


async def _airport_code(value: str, field: str, context) -> str:
    code = value.strip().upper()
    if not IATA_AIRPORT.match(code):
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"{field} must be a 3-letter IATA airport code")
    return code


async def _validate_price(price: flight_pb2.Money, present: bool, context) -> None:
    if not present or price.amount_minor <= 0 or not re.match(r"^[A-Z]{3}$", price.currency_code):
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                            "price must have a positive amount_minor and an ISO 4217 currency_code")


async def _validate_schedule(origin: str, destination: str, departure, arrival, context) -> None:
    if origin == destination:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "origin and destination must differ")
    if arrival <= departure:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "arrival_time must be after departure_time")
