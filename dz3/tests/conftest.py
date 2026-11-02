import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import grpc
import httpx
import pytest
from google.protobuf.timestamp_pb2 import Timestamp
from redis.asyncio.sentinel import Sentinel

from flight.v1 import flight_pb2, flight_pb2_grpc

API_KEY = os.environ["FLIGHT_SERVICE_API_KEY"]
AUTH = [("x-api-key", API_KEY)]


@pytest.fixture(scope="session")
async def channel():
    async with grpc.aio.insecure_channel(os.environ["FLIGHT_SERVICE_ADDRESS"]) as ch:
        yield ch


@pytest.fixture(scope="session")
def stub(channel) -> flight_pb2_grpc.FlightServiceStub:
    return flight_pb2_grpc.FlightServiceStub(channel)


@pytest.fixture(scope="session")
async def api():
    async with httpx.AsyncClient(base_url=os.environ["BOOKING_URL"], timeout=30) as client:
        yield client


@pytest.fixture(scope="session")
async def redis():
    hosts = [(h, int(p)) for h, p in (item.rsplit(":", 1) for item in os.environ["REDIS_SENTINELS"].split(","))]
    client = Sentinel(hosts).master_for(os.environ["REDIS_MASTER_NAME"])
    yield client
    await client.aclose()


def timestamp(value: datetime) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(value)
    return ts


@pytest.fixture
def make_flight(stub):
    async def create(total_seats: int = 10, price_minor: int = 500000, origin: str = "KZN",
                     destination: str = "AER") -> flight_pb2.Flight:
        departure = datetime(2027, 1, 1, 10, tzinfo=timezone.utc) + timedelta(days=random.randrange(3000))
        request = flight_pb2.CreateFlightRequest(
            flight_number=f"SU{random.randrange(1, 10000)}",
            origin=origin,
            destination=destination,
            departure_time=timestamp(departure),
            arrival_time=timestamp(departure + timedelta(hours=2)),
            total_seats=total_seats,
            price=flight_pb2.Money(currency_code="RUB", amount_minor=price_minor),
        )
        return await stub.CreateFlight(request, metadata=AUTH)

    return create


def booking_body(flight_id: str, seat_count: int = 1, user_id: str | None = None) -> dict:
    return {
        "user_id": user_id or f"user-{uuid.uuid4()}",
        "flight_id": flight_id,
        "passenger_name": "Ivan Petrov",
        "passenger_email": "ivan@example.com",
        "seat_count": seat_count,
    }
