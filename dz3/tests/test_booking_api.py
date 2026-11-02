import asyncio
import uuid

from conftest import AUTH, booking_body
from flight.v1 import flight_pb2


async def available_seats(api, flight_id: str) -> int:
    return (await api.get(f"/flights/{flight_id}")).json()["available_seats"]


async def test_search_and_get_flight(api, make_flight):
    flight = await make_flight(origin="LED", destination="KZN", price_minor=432100)
    day = flight.departure_time.ToDatetime().date().isoformat()

    response = await api.get("/flights", params={"origin": "LED", "destination": "KZN", "date": day})
    assert response.status_code == 200
    found = {f["id"]: f for f in response.json()["flights"]}
    assert found[flight.id]["price"] == {"amount": "4321.00", "currency": "RUB"}
    assert found[flight.id]["status"] == "SCHEDULED"
    assert all(f["status"] == "SCHEDULED" for f in response.json()["flights"])

    response = await api.get(f"/flights/{flight.id}")
    assert response.status_code == 200
    assert response.json()["flight_number"] == flight.flight_number


async def test_flight_errors(api):
    assert (await api.get(f"/flights/{uuid.uuid4()}")).status_code == 404
    assert (await api.get("/flights/not-a-uuid")).status_code == 400
    assert (await api.get("/flights", params={"origin": "SVO"})).status_code == 400
    assert (await api.get("/flights", params={"origin": "SVO", "destination": "LED", "date": "x"})).status_code == 400


async def test_create_get_list_cancel_booking(api, make_flight):
    flight = await make_flight(total_seats=10, price_minor=250050)
    user = f"user-{uuid.uuid4()}"

    response = await api.post("/bookings", json=booking_body(flight.id, seat_count=3, user_id=user))
    assert response.status_code == 201
    booking = response.json()
    assert booking["status"] == "CONFIRMED"
    assert booking["total_price"] == {"amount": "7501.50", "currency": "RUB"}
    assert await available_seats(api, flight.id) == 7

    assert (await api.get(f"/bookings/{booking['id']}")).json() == booking
    listed = (await api.get("/bookings", params={"user_id": user})).json()["bookings"]
    assert [b["id"] for b in listed] == [booking["id"]]

    response = await api.post(f"/bookings/{booking['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert await available_seats(api, flight.id) == 10

    response = await api.post(f"/bookings/{booking['id']}/cancel")
    assert response.status_code == 409
    assert await available_seats(api, flight.id) == 10


async def test_total_price_is_a_snapshot(api, stub, make_flight):
    flight = await make_flight(price_minor=100000)
    booking = (await api.post("/bookings", json=booking_body(flight.id, seat_count=2))).json()
    await stub.UpdateFlight(flight_pb2.UpdateFlightRequest(
        flight_id=flight.id, price=flight_pb2.Money(currency_code="RUB", amount_minor=900000)), metadata=AUTH)
    assert (await api.get(f"/bookings/{booking['id']}")).json()["total_price"]["amount"] == "2000.00"


async def test_booking_is_not_created_when_reservation_fails(api, make_flight):
    flight = await make_flight(total_seats=2)
    user = f"user-{uuid.uuid4()}"

    response = await api.post("/bookings", json=booking_body(flight.id, seat_count=3, user_id=user))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NOT_ENOUGH_SEATS"

    response = await api.post("/bookings", json=booking_body(str(uuid.uuid4()), user_id=user))
    assert response.status_code == 404

    assert (await api.get("/bookings", params={"user_id": user})).json()["bookings"] == []
    assert await available_seats(api, flight.id) == 2


async def test_booking_a_departed_flight_is_rejected(api, stub, make_flight):
    flight = await make_flight()
    await stub.UpdateFlight(flight_pb2.UpdateFlightRequest(flight_id=flight.id,
                                                           status=flight_pb2.FLIGHT_STATUS_DEPARTED), metadata=AUTH)
    response = await api.post("/bookings", json=booking_body(flight.id))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FLIGHT_NOT_AVAILABLE"


async def test_last_seat_is_sold_once(api, make_flight):
    flight = await make_flight(total_seats=1)
    responses = await asyncio.gather(*(api.post("/bookings", json=booking_body(flight.id)) for _ in range(10)))
    codes = sorted(r.status_code for r in responses)
    assert codes == [201] + [409] * 9
    assert await available_seats(api, flight.id) == 0


async def test_booking_validation(api):
    response = await api.post("/bookings", json={**booking_body(str(uuid.uuid4())), "seat_count": 0})
    assert response.status_code == 400
    response = await api.post("/bookings", json={**booking_body(str(uuid.uuid4())), "passenger_email": "nope"})
    assert response.status_code == 400
    assert (await api.get("/bookings")).status_code == 400
    assert (await api.get(f"/bookings/{uuid.uuid4()}")).status_code == 404
    assert (await api.post(f"/bookings/{uuid.uuid4()}/cancel")).status_code == 404
