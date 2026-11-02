import grpc

from booking_service.config import Settings
from booking_service.grpc_middleware.auth import ApiKeyInterceptor
from booking_service.grpc_middleware.circuit_breaker import CircuitBreaker, CircuitBreakerInterceptor
from booking_service.grpc_middleware.retry import RetryInterceptor
from flight.v1 import flight_pb2, flight_pb2_grpc

CHANNEL_OPTIONS = [
    ("grpc.enable_retries", 0),
    ("grpc.initial_reconnect_backoff_ms", 100),
    ("grpc.min_reconnect_backoff_ms", 100),
    ("grpc.max_reconnect_backoff_ms", 1000),
]


class FlightClient:
    def __init__(self, settings: Settings):
        self.breaker = CircuitBreaker(settings.circuit_breaker)
        # Outermost first: the breaker sees one outcome per logical call, after all retries.
        self._channel = grpc.aio.insecure_channel(
            settings.flight_service_address,
            options=CHANNEL_OPTIONS,
            interceptors=[
                CircuitBreakerInterceptor(self.breaker),
                RetryInterceptor(settings.retry),
                ApiKeyInterceptor(settings.flight_service_api_key),
            ],
        )
        self._stub = flight_pb2_grpc.FlightServiceStub(self._channel)
        self._timeout = settings.grpc_timeout_seconds

    async def search_flights(self, request: flight_pb2.SearchFlightsRequest) -> flight_pb2.SearchFlightsResponse:
        return await self._stub.SearchFlights(request, timeout=self._timeout)

    async def get_flight(self, flight_id: str) -> flight_pb2.Flight:
        return await self._stub.GetFlight(flight_pb2.GetFlightRequest(flight_id=flight_id), timeout=self._timeout)

    async def reserve_seats(self, flight_id: str, seat_count: int, booking_id: str) -> flight_pb2.SeatReservation:
        request = flight_pb2.ReserveSeatsRequest(flight_id=flight_id, seat_count=seat_count, booking_id=booking_id)
        return await self._stub.ReserveSeats(request, timeout=self._timeout)

    async def release_reservation(self, booking_id: str) -> flight_pb2.SeatReservation:
        request = flight_pb2.ReleaseReservationRequest(booking_id=booking_id)
        return await self._stub.ReleaseReservation(request, timeout=self._timeout)

    async def close(self) -> None:
        await self._channel.close()
