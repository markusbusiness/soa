import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

log = logging.getLogger("aggregator.retry")


async def with_retry(operation: str, func: Callable[[], Awaitable[T]], attempts: int, base_seconds: float,
                     max_seconds: float = 30) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except Exception as exc:
            if attempt == attempts:
                log.error("%s failed after %d attempts: %r", operation, attempt, exc)
                raise
            delay = random.uniform(0, min(max_seconds, base_seconds * 2 ** (attempt - 1))) + base_seconds / 2
            log.warning("%s failed (attempt %d/%d), retry in %.2fs: %r", operation, attempt, attempts, delay, exc)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
