import logging
from datetime import date

from redis.asyncio.sentinel import Sentinel
from redis.exceptions import RedisError

logger = logging.getLogger("flight_service.cache")


def flight_key(flight_id: str) -> str:
    return f"flight:{flight_id}"


def search_key(origin: str, destination: str, departure_date: date | None) -> str:
    return f"search:{origin}:{destination}:{departure_date.isoformat() if departure_date else 'any'}"


def route_keys(origin: str, destination: str, departure_date: date) -> list[str]:
    return [search_key(origin, destination, departure_date), search_key(origin, destination, None)]


class FlightCache:
    """Cache-aside storage on the Sentinel-managed Redis master. Redis errors degrade to cache misses."""

    def __init__(self, sentinels: list[tuple[str, int]], master_name: str, timeout: float, ttl_seconds: int):
        self._sentinel = Sentinel(sentinels, socket_timeout=timeout, sentinel_kwargs={"socket_timeout": timeout})
        self._redis = self._sentinel.master_for(master_name, socket_timeout=timeout,
                                                socket_connect_timeout=timeout)
        self._ttl = ttl_seconds

    async def get(self, key: str) -> bytes | None:
        try:
            value = await self._redis.get(key)
        except RedisError as exc:
            logger.warning("cache unavailable on get %s: %r", key, exc)
            return None
        logger.info("cache %s key=%s", "hit" if value is not None else "miss", key)
        return value

    async def set(self, key: str, value: bytes) -> None:
        try:
            await self._redis.set(key, value, ex=self._ttl)
        except RedisError as exc:
            logger.warning("cache unavailable on set %s: %r", key, exc)

    async def invalidate(self, keys: list[str]) -> None:
        try:
            await self._redis.delete(*keys)
            logger.info("cache invalidated keys=%s", ",".join(keys))
        except RedisError as exc:
            logger.error("cache invalidation failed keys=%s: %r", ",".join(keys), exc)

    async def close(self) -> None:
        await self._redis.aclose()
