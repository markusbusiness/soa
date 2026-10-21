from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from marketplace.config import Settings


class Runtime:
    settings: Settings
    pool: asyncpg.Pool

    async def start(self, settings: Settings) -> None:
        self.settings = settings
        self.pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=settings.db_pool_max_size)

    async def stop(self) -> None:
        await self.pool.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield conn


runtime = Runtime()
