import uuid
from datetime import timezone

from conftest import AUTH
from flight.v1 import flight_pb2


def route_keys(flight: flight_pb2.Flight) -> list[str]:
    day = flight.departure_time.ToDatetime(tzinfo=timezone.utc).date().isoformat()
    route = f"{flight.origin.iata_code}:{flight.destination.iata_code}"
    return [f"search:{route}:{day}", f"search:{route}:any"]


async def warm(stub, flight: flight_pb2.Flight) -> None:
    await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight.id), metadata=AUTH)
    route = dict(origin=flight.origin.iata_code, destination=flight.destination.iata_code)
    await stub.SearchFlights(flight_pb2.SearchFlightsRequest(**route), metadata=AUTH)
    await stub.SearchFlights(flight_pb2.SearchFlightsRequest(
        **route, departure_date=flight.departure_time), metadata=AUTH)


async def test_reads_are_cached_with_ttl(stub, redis, make_flight):
    flight = await make_flight()
    await warm(stub, flight)
    for key in [f"flight:{flight.id}", *route_keys(flight)]:
        assert await redis.exists(key), key
        assert 0 < await redis.ttl(key) <= 600, key
    cached = flight_pb2.Flight.FromString(await redis.get(f"flight:{flight.id}"))
    assert cached == flight


async def test_mutations_invalidate_cache(stub, redis, make_flight):
    flight = await make_flight(total_seats=4)
    keys = [f"flight:{flight.id}", *route_keys(flight)]
    booking_id = str(uuid.uuid4())

    await warm(stub, flight)
    await stub.ReserveSeats(flight_pb2.ReserveSeatsRequest(flight_id=flight.id, seat_count=1, booking_id=booking_id),
                            metadata=AUTH)
    assert await redis.exists(*keys) == 0
    assert (await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight.id), metadata=AUTH)).available_seats == 3

    await warm(stub, flight)
    await stub.ReleaseReservation(flight_pb2.ReleaseReservationRequest(booking_id=booking_id), metadata=AUTH)
    assert await redis.exists(*keys) == 0
    assert (await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight.id), metadata=AUTH)).available_seats == 4

    await warm(stub, flight)
    await stub.UpdateFlight(flight_pb2.UpdateFlightRequest(
        flight_id=flight.id, price=flight_pb2.Money(currency_code="RUB", amount_minor=999900)), metadata=AUTH)
    assert await redis.exists(*keys) == 0
    updated = await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight.id), metadata=AUTH)
    assert updated.price.amount_minor == 999900


async def test_rescheduling_invalidates_old_and_new_date(stub, redis, make_flight):
    flight = await make_flight()
    moved = flight_pb2.Flight()
    moved.CopyFrom(flight)
    moved.departure_time.seconds += 86400
    moved.arrival_time.seconds += 86400

    await warm(stub, flight)
    await warm(stub, moved)
    await stub.UpdateFlight(flight_pb2.UpdateFlightRequest(
        flight_id=flight.id, departure_time=moved.departure_time, arrival_time=moved.arrival_time), metadata=AUTH)
    assert await redis.exists(*route_keys(flight), *route_keys(moved)) == 0
