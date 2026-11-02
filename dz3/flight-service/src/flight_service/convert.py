from datetime import date, datetime, timezone

import asyncpg
from google.protobuf.timestamp_pb2 import Timestamp

from flight.v1 import flight_pb2


def to_timestamp(value: datetime) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(value.astimezone(timezone.utc))
    return ts


def from_timestamp(ts: Timestamp) -> datetime:
    return ts.ToDatetime(tzinfo=timezone.utc)


def utc_date(value: datetime) -> date:
    return value.astimezone(timezone.utc).date()


def flight_status(name: str) -> int:
    return flight_pb2.FlightStatus.Value(f"FLIGHT_STATUS_{name}")


def flight_status_name(value: int) -> str:
    return flight_pb2.FlightStatus.Name(value).removeprefix("FLIGHT_STATUS_")


def flight_to_proto(row: asyncpg.Record) -> flight_pb2.Flight:
    return flight_pb2.Flight(
        id=str(row["id"]),
        flight_number=f"{row['airline_code']}{row['number']}",
        airline=flight_pb2.Airline(iata_code=row["airline_code"], name=row["airline_name"]),
        origin=flight_pb2.Airport(iata_code=row["origin_code"], name=row["origin_name"], city=row["origin_city"]),
        destination=flight_pb2.Airport(iata_code=row["destination_code"], name=row["destination_name"],
                                       city=row["destination_city"]),
        departure_time=to_timestamp(row["departure_time"]),
        arrival_time=to_timestamp(row["arrival_time"]),
        total_seats=row["total_seats"],
        available_seats=row["available_seats"],
        price=flight_pb2.Money(currency_code=row["currency_code"], amount_minor=row["price_minor"]),
        status=flight_status(row["status"]),
    )


def reservation_to_proto(row: asyncpg.Record) -> flight_pb2.SeatReservation:
    return flight_pb2.SeatReservation(
        id=str(row["id"]),
        flight_id=str(row["flight_id"]),
        booking_id=str(row["booking_id"]),
        seat_count=row["seat_count"],
        status=flight_pb2.ReservationStatus.Value(f"RESERVATION_STATUS_{row['status']}"),
        created_at=to_timestamp(row["created_at"]),
        updated_at=to_timestamp(row["updated_at"]),
    )
