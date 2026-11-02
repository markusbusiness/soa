import asyncio
import time

import grpc
import pytest

from booking_service.config import CircuitBreakerSettings, RetrySettings, Settings
from booking_service.flight_client import FlightClient
from booking_service.grpc_middleware.circuit_breaker import CircuitOpenError, State
from flight.v1 import flight_pb2, flight_pb2_grpc

FLIGHT_ID = "11111111-1111-1111-1111-111111111101"


class ScriptedFlightService(flight_pb2_grpc.FlightServiceServicer):
    """Answers GetFlight with the next scripted outcome: a status code, "slow", or "ok" once the script runs out."""

    def __init__(self):
        self.script: list = []
        self.calls = 0
        self.api_keys: list[str | None] = []

    async def GetFlight(self, request, context):
        self.calls += 1
        self.api_keys.append(dict(context.invocation_metadata()).get("x-api-key"))
        outcome = self.script.pop(0) if self.script else "ok"
        if outcome == "slow":
            await asyncio.sleep(1)
        elif outcome != "ok":
            await context.abort(outcome, f"scripted {outcome.name}")
        return flight_pb2.Flight(id=request.flight_id)


@pytest.fixture
async def fake():
    service = ScriptedFlightService()
    server = grpc.aio.server()
    flight_pb2_grpc.add_FlightServiceServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    service.address = f"127.0.0.1:{port}"
    yield service
    await server.stop(None)


def client_for(service, max_attempts=3, failure_threshold=100, open_timeout=0.5, timeout=0.3) -> FlightClient:
    return FlightClient(Settings(
        database_url="",
        flight_service_address=service.address,
        flight_service_api_key="test-key",
        grpc_timeout_seconds=timeout,
        retry=RetrySettings(max_attempts=max_attempts, base_delay_seconds=0.1, multiplier=2),
        circuit_breaker=CircuitBreakerSettings(failure_threshold=failure_threshold, window_seconds=30,
                                               open_timeout_seconds=open_timeout),
        pending_reservation_timeout_seconds=60,
        sweeper_interval_seconds=10,
        db_pool_max_size=1,
    ))


async def test_api_key_is_sent_in_metadata(fake):
    await client_for(fake).get_flight(FLIGHT_ID)
    assert fake.api_keys == ["test-key"]


async def test_retries_unavailable_with_exponential_backoff(fake):
    fake.script = [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.UNAVAILABLE]
    started = time.monotonic()
    flight = await client_for(fake).get_flight(FLIGHT_ID)
    elapsed = time.monotonic() - started
    assert flight.id == FLIGHT_ID
    assert fake.calls == 3
    assert 0.3 <= elapsed < 1.0


async def test_retries_deadline_exceeded(fake):
    fake.script = ["slow"]
    assert (await client_for(fake).get_flight(FLIGHT_ID)).id == FLIGHT_ID
    assert fake.calls == 2


async def test_gives_up_after_three_attempts(fake):
    fake.script = [grpc.StatusCode.UNAVAILABLE] * 5
    with pytest.raises(grpc.aio.AioRpcError) as info:
        await client_for(fake).get_flight(FLIGHT_ID)
    assert info.value.code() == grpc.StatusCode.UNAVAILABLE
    assert fake.calls == 3


@pytest.mark.parametrize("code", [grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.NOT_FOUND,
                                  grpc.StatusCode.RESOURCE_EXHAUSTED, grpc.StatusCode.FAILED_PRECONDITION])
async def test_business_errors_are_not_retried(fake, code):
    fake.script = [code]
    with pytest.raises(grpc.aio.AioRpcError) as info:
        await client_for(fake).get_flight(FLIGHT_ID)
    assert info.value.code() == code
    assert fake.calls == 1


async def test_circuit_breaker_full_cycle(fake):
    client = client_for(fake, max_attempts=1, failure_threshold=3, open_timeout=0.5)
    fake.script = [grpc.StatusCode.UNAVAILABLE] * 3
    for _ in range(3):
        with pytest.raises(grpc.aio.AioRpcError):
            await client.get_flight(FLIGHT_ID)
    assert client.breaker.state is State.OPEN

    with pytest.raises(CircuitOpenError):
        await client.get_flight(FLIGHT_ID)
    assert fake.calls == 3

    await asyncio.sleep(0.6)
    fake.script = [grpc.StatusCode.UNAVAILABLE]
    with pytest.raises(grpc.aio.AioRpcError):
        await client.get_flight(FLIGHT_ID)
    assert client.breaker.state is State.OPEN
    assert fake.calls == 4

    await asyncio.sleep(0.6)
    assert (await client.get_flight(FLIGHT_ID)).id == FLIGHT_ID
    assert client.breaker.state is State.CLOSED


async def test_half_open_lets_a_single_probe_through(fake):
    client = client_for(fake, max_attempts=1, failure_threshold=1, open_timeout=0.2, timeout=2)
    fake.script = [grpc.StatusCode.UNAVAILABLE, "slow"]
    with pytest.raises(grpc.aio.AioRpcError):
        await client.get_flight(FLIGHT_ID)
    await asyncio.sleep(0.3)

    probe = asyncio.create_task(client.get_flight(FLIGHT_ID))
    await asyncio.sleep(0.1)
    assert client.breaker.state is State.HALF_OPEN
    with pytest.raises(CircuitOpenError):
        await client.get_flight(FLIGHT_ID)
    assert (await probe).id == FLIGHT_ID
    assert client.breaker.state is State.CLOSED
    assert fake.calls == 2


async def test_business_errors_do_not_open_the_circuit(fake):
    client = client_for(fake, max_attempts=1, failure_threshold=2)
    fake.script = [grpc.StatusCode.NOT_FOUND] * 5
    for _ in range(5):
        with pytest.raises(grpc.aio.AioRpcError):
            await client.get_flight(FLIGHT_ID)
    assert client.breaker.state is State.CLOSED
