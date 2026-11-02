import asyncio
import uuid

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from conftest import AUTH
from flight.v1 import flight_pb2

UNKNOWN_FLIGHT = "00000000-0000-0000-0000-000000000000"


async def rpc_error(awaitable) -> grpc.aio.AioRpcError:
    with pytest.raises(grpc.aio.AioRpcError) as info:
        await awaitable
    return info.value


@pytest.mark.parametrize("metadata", [None, [("x-api-key", "wrong")]], ids=["missing", "invalid"])
async def test_every_method_requires_api_key(stub, channel, metadata):
    calls = [
        stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=UNKNOWN_FLIGHT), metadata=metadata),
        stub.SearchFlights(flight_pb2.SearchFlightsRequest(origin="SVO", destination="LED"), metadata=metadata),
        stub.ReserveSeats(flight_pb2.ReserveSeatsRequest(flight_id=UNKNOWN_FLIGHT, seat_count=1,
                                                         booking_id=str(uuid.uuid4())), metadata=metadata),
        stub.ReleaseReservation(flight_pb2.ReleaseReservationRequest(booking_id=str(uuid.uuid4())),
                                metadata=metadata),
        health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest(), metadata=metadata),
    ]
    for call in calls:
        assert (await rpc_error(call)).code() == grpc.StatusCode.UNAUTHENTICATED


async def test_get_flight(stub, make_flight):
    created = await make_flight(total_seats=7, price_minor=123456)
    flight = await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=created.id), metadata=AUTH)
    assert flight == created
    assert flight.status == flight_pb2.FLIGHT_STATUS_SCHEDULED
    assert (flight.total_seats, flight.available_seats) == (7, 7)
    assert flight.price == flight_pb2.Money(currency_code="RUB", amount_minor=123456)


async def test_get_flight_errors(stub):
    error = await rpc_error(stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=UNKNOWN_FLIGHT), metadata=AUTH))
    assert error.code() == grpc.StatusCode.NOT_FOUND
    error = await rpc_error(stub.GetFlight(flight_pb2.GetFlightRequest(flight_id="nope"), metadata=AUTH))
    assert error.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_search_by_route_and_date(stub, make_flight):
    flight = await make_flight(origin="SVX", destination="KZN")

    found = await stub.SearchFlights(
        flight_pb2.SearchFlightsRequest(origin="SVX", destination="KZN", departure_date=flight.departure_time), metadata=AUTH)
    assert flight.id in [f.id for f in found.flights]
    found = await stub.SearchFlights(flight_pb2.SearchFlightsRequest(origin="SVX", destination="KZN"), metadata=AUTH)
    assert flight.id in [f.id for f in found.flights]
    found = await stub.SearchFlights(flight_pb2.SearchFlightsRequest(origin="KZN", destination="SVX"), metadata=AUTH)
    assert flight.id not in [f.id for f in found.flights]


async def test_search_returns_only_scheduled(stub, make_flight):
    flight = await make_flight(origin="OVB", destination="SVX")
    await stub.UpdateFlight(flight_pb2.UpdateFlightRequest(flight_id=flight.id,
                                                           status=flight_pb2.FLIGHT_STATUS_CANCELLED), metadata=AUTH)
    found = await stub.SearchFlights(flight_pb2.SearchFlightsRequest(origin="OVB", destination="SVX"), metadata=AUTH)
    assert flight.id not in [f.id for f in found.flights]


async def test_reserve_and_release(stub, make_flight):
    flight = await make_flight(total_seats=5)
    booking_id = str(uuid.uuid4())
    reservation = await stub.ReserveSeats(
        flight_pb2.ReserveSeatsRequest(flight_id=flight.id, seat_count=3, booking_id=booking_id), metadata=AUTH)
    assert reservation.status == flight_pb2.RESERVATION_STATUS_ACTIVE
    assert (await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight.id), metadata=AUTH)).available_seats == 2

    released = await stub.ReleaseReservation(flight_pb2.ReleaseReservationRequest(booking_id=booking_id), metadata=AUTH)
    assert released.status == flight_pb2.RESERVATION_STATUS_RELEASED
    assert released.id == reservation.id
    assert (await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight.id), metadata=AUTH)).available_seats == 5

    again = await stub.ReleaseReservation(flight_pb2.ReleaseReservationRequest(booking_id=booking_id), metadata=AUTH)
    assert again.status == flight_pb2.RESERVATION_STATUS_RELEASED
    assert (await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight.id), metadata=AUTH)).available_seats == 5


async def test_reserve_is_idempotent_by_booking_id(stub, make_flight):
    flight = await make_flight(total_seats=5)
    request = flight_pb2.ReserveSeatsRequest(flight_id=flight.id, seat_count=2, booking_id=str(uuid.uuid4()))
    first = await stub.ReserveSeats(request, metadata=AUTH)
    replays = await asyncio.gather(*(stub.ReserveSeats(request, metadata=AUTH) for _ in range(5)))
    assert {r.id for r in replays} == {first.id}
    assert (await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight.id), metadata=AUTH)).available_seats == 3

    other = flight_pb2.ReserveSeatsRequest(flight_id=flight.id, seat_count=1, booking_id=request.booking_id)
    assert (await rpc_error(stub.ReserveSeats(other, metadata=AUTH))).code() == grpc.StatusCode.FAILED_PRECONDITION


async def test_reserve_errors(stub, make_flight):
    flight = await make_flight(total_seats=2)

    def reserve(flight_id: str, seats: int):
        return stub.ReserveSeats(flight_pb2.ReserveSeatsRequest(flight_id=flight_id, seat_count=seats,
                                                                booking_id=str(uuid.uuid4())), metadata=AUTH)

    assert (await rpc_error(reserve(flight.id, 3))).code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert (await rpc_error(reserve(flight.id, 0))).code() == grpc.StatusCode.INVALID_ARGUMENT
    assert (await rpc_error(reserve(UNKNOWN_FLIGHT, 1))).code() == grpc.StatusCode.NOT_FOUND
    await stub.UpdateFlight(flight_pb2.UpdateFlightRequest(flight_id=flight.id,
                                                           status=flight_pb2.FLIGHT_STATUS_DEPARTED), metadata=AUTH)
    assert (await rpc_error(reserve(flight.id, 1))).code() == grpc.StatusCode.FAILED_PRECONDITION
    error = await rpc_error(stub.ReleaseReservation(
        flight_pb2.ReleaseReservationRequest(booking_id=str(uuid.uuid4())), metadata=AUTH))
    assert error.code() == grpc.StatusCode.NOT_FOUND


async def test_concurrent_reservations_never_oversell(stub, make_flight):
    flight = await make_flight(total_seats=3)
    results = await asyncio.gather(
        *(stub.ReserveSeats(flight_pb2.ReserveSeatsRequest(flight_id=flight.id, seat_count=1,
                                                           booking_id=str(uuid.uuid4())), metadata=AUTH)
          for _ in range(20)),
        return_exceptions=True,
    )
    succeeded = [r for r in results if isinstance(r, flight_pb2.SeatReservation)]
    failed = [r for r in results if isinstance(r, grpc.aio.AioRpcError)]
    assert len(succeeded) == 3
    assert {e.code() for e in failed} == {grpc.StatusCode.RESOURCE_EXHAUSTED}
    assert len(failed) == 17
    assert (await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight.id), metadata=AUTH)).available_seats == 0


async def test_create_flight_is_unique_per_number_and_date(stub, make_flight):
    flight = await make_flight()
    duplicate = flight_pb2.CreateFlightRequest(
        flight_number=flight.flight_number, origin="SVO", destination="LED",
        departure_time=flight.departure_time, arrival_time=flight.arrival_time, total_seats=10,
        price=flight_pb2.Money(currency_code="RUB", amount_minor=100),
    )
    assert (await rpc_error(stub.CreateFlight(duplicate, metadata=AUTH))).code() == grpc.StatusCode.ALREADY_EXISTS


async def test_update_flight_status_transitions(stub, make_flight):
    flight = await make_flight()

    def update(status):
        return stub.UpdateFlight(flight_pb2.UpdateFlightRequest(flight_id=flight.id, status=status), metadata=AUTH)

    assert (await rpc_error(update(flight_pb2.FLIGHT_STATUS_COMPLETED))).code() == grpc.StatusCode.FAILED_PRECONDITION
    assert (await update(flight_pb2.FLIGHT_STATUS_DEPARTED)).status == flight_pb2.FLIGHT_STATUS_DEPARTED
    assert (await update(flight_pb2.FLIGHT_STATUS_COMPLETED)).status == flight_pb2.FLIGHT_STATUS_COMPLETED
    assert (await rpc_error(update(flight_pb2.FLIGHT_STATUS_SCHEDULED))).code() == grpc.StatusCode.FAILED_PRECONDITION
