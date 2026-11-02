import asyncio
import os
import uuid

import asyncpg

from conftest import AUTH
from flight.v1 import flight_pb2


async def test_orphaned_reservation_is_released(stub, make_flight):
    flight = await make_flight(total_seats=4)
    booking_id = uuid.uuid4()
    await stub.ReserveSeats(flight_pb2.ReserveSeatsRequest(flight_id=flight.id, seat_count=2,
                                                           booking_id=str(booking_id)), metadata=AUTH)
    conn = await asyncpg.connect(os.environ["BOOKING_DATABASE_URL"])
    try:
        await conn.execute(
            "INSERT INTO pending_reservations (booking_id, flight_id, created_at) VALUES ($1, $2, now() - interval '1 hour')",
            booking_id, uuid.UUID(flight.id),
        )
        for _ in range(60):
            if await conn.fetchval("SELECT count(*) FROM pending_reservations WHERE booking_id = $1", booking_id) == 0:
                break
            await asyncio.sleep(0.5)
        else:
            raise AssertionError("sweeper did not process the pending reservation")
    finally:
        await conn.close()

    released = await stub.ReleaseReservation(flight_pb2.ReleaseReservationRequest(booking_id=str(booking_id)),
                                             metadata=AUTH)
    assert released.status == flight_pb2.RESERVATION_STATUS_RELEASED
    assert (await stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight.id), metadata=AUTH)).available_seats == 4
