from datetime import timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from flight.v1 import flight_pb2


class CreateBookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=64)
    flight_id: UUID
    passenger_name: str = Field(min_length=1, max_length=200)
    passenger_email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    seat_count: int = Field(ge=1, le=1000)


def money(amount_minor: int, currency_code: str) -> dict[str, str]:
    return {"amount": str(Decimal(amount_minor).scaleb(-2).quantize(Decimal("0.01"))), "currency": currency_code}


def flight_json(flight: flight_pb2.Flight) -> dict[str, Any]:
    def airport(a: flight_pb2.Airport) -> dict[str, str]:
        return {"iata_code": a.iata_code, "name": a.name, "city": a.city}

    return {
        "id": flight.id,
        "flight_number": flight.flight_number,
        "airline": {"iata_code": flight.airline.iata_code, "name": flight.airline.name},
        "origin": airport(flight.origin),
        "destination": airport(flight.destination),
        "departure_time": flight.departure_time.ToDatetime(tzinfo=timezone.utc).isoformat(),
        "arrival_time": flight.arrival_time.ToDatetime(tzinfo=timezone.utc).isoformat(),
        "total_seats": flight.total_seats,
        "available_seats": flight.available_seats,
        "price": money(flight.price.amount_minor, flight.price.currency_code),
        "status": flight_pb2.FlightStatus.Name(flight.status).removeprefix("FLIGHT_STATUS_"),
    }


def booking_json(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": row["user_id"],
        "flight_id": str(row["flight_id"]),
        "passenger_name": row["passenger_name"],
        "passenger_email": row["passenger_email"],
        "seat_count": row["seat_count"],
        "total_price": money(row["total_price_minor"], row["currency_code"]),
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }
