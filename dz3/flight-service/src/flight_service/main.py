import asyncio
import logging
import signal

import asyncpg
import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

from flight.v1 import flight_pb2, flight_pb2_grpc
from flight_service.auth import ApiKeyInterceptor
from flight_service.cache import FlightCache
from flight_service.config import load_settings
from flight_service.servicer import FlightServicer

logger = logging.getLogger("flight_service")


async def serve() -> None:
    settings = load_settings()
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=settings.db_pool_max_size)
    cache = FlightCache(settings.redis_sentinels, settings.redis_master_name, settings.redis_timeout_seconds,
                        settings.cache_ttl_seconds)

    server = grpc.aio.server(interceptors=[ApiKeyInterceptor(settings.api_key)])
    flight_pb2_grpc.add_FlightServiceServicer_to_server(FlightServicer(pool, cache), server)
    health_servicer = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    service_name = flight_pb2.DESCRIPTOR.services_by_name["FlightService"].full_name
    reflection.enable_server_reflection((service_name, health.SERVICE_NAME, reflection.SERVICE_NAME), server)
    server.add_insecure_port(f"0.0.0.0:{settings.grpc_port}")
    await server.start()
    await health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    logger.info("flight service listening on :%d", settings.grpc_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    logger.info("shutting down")
    await server.stop(grace=5)
    await cache.close()
    await pool.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(serve())


if __name__ == "__main__":
    main()
