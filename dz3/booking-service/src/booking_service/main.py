import asyncio
import contextlib
import logging
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone
from uuid import UUID

import asyncpg
import grpc
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from booking_service.bookings import BookingService
from booking_service.config import load_settings
from booking_service.errors import ApiError, from_grpc, install_error_handlers
from booking_service.flight_client import FlightClient
from booking_service.schemas import CreateBookingRequest, booking_json, flight_json
from booking_service.sweeper import PendingReservationSweeper
from flight.v1 import flight_pb2

IATA_AIRPORT = re.compile(r"^[A-Za-z]{3}$")


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=settings.db_pool_max_size)
        flights = FlightClient(settings)
        app.state.flights = flights
        app.state.bookings = BookingService(pool, flights)
        sweeper = PendingReservationSweeper(pool, flights, settings.pending_reservation_timeout_seconds,
                                            settings.sweeper_interval_seconds)
        sweeper_task = asyncio.create_task(sweeper.run())
        yield
        sweeper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper_task
        await flights.close()
        await pool.close()

    app = FastAPI(title="Booking Service", lifespan=lifespan)
    install_error_handlers(app)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/flights")
    async def search_flights(request: Request, origin: str = Query(), destination: str = Query(),
                             date: date | None = Query(default=None)):
        for name, value in (("origin", origin), ("destination", destination)):
            if not IATA_AIRPORT.match(value):
                raise ApiError(400, "VALIDATION_ERROR", f"{name}: must be a 3-letter IATA airport code")
        search = flight_pb2.SearchFlightsRequest(origin=origin.upper(), destination=destination.upper())
        if date is not None:
            search.departure_date.FromDatetime(datetime.combine(date, time.min, tzinfo=timezone.utc))
        response = await request.app.state.flights.search_flights(search)
        return {"flights": [flight_json(f) for f in response.flights]}

    @app.get("/flights/{flight_id}")
    async def get_flight(request: Request, flight_id: UUID):
        return flight_json(await request.app.state.flights.get_flight(str(flight_id)))

    @app.post("/bookings", status_code=201)
    async def create_booking(request: Request, body: CreateBookingRequest):
        return booking_json(await request.app.state.bookings.create(body))

    @app.get("/bookings")
    async def list_bookings(request: Request, user_id: str = Query(min_length=1, max_length=64)):
        return {"bookings": [booking_json(b) for b in await request.app.state.bookings.list_for_user(user_id)]}

    @app.get("/bookings/{booking_id}")
    async def get_booking(request: Request, booking_id: UUID):
        return booking_json(await request.app.state.bookings.get(booking_id))

    @app.post("/bookings/{booking_id}/cancel")
    async def cancel_booking(request: Request, booking_id: UUID):
        return booking_json(await request.app.state.bookings.cancel(booking_id))

    return app
