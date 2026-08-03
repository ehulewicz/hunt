from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol
from uuid import UUID

from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from personal_os.config import Settings


class BootstrapStore(Protocol):
    async def bootstrap(self, user_id: UUID, email: str) -> None: ...


class PostgresBootstrapStore:
    def __init__(self, pool: AsyncConnectionPool, timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def bootstrap(self, user_id: UUID, email: str) -> None:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            await connection.execute(
                """
                insert into app.jobs_profile (user_id, email)
                values (%s, %s)
                on conflict (user_id) do nothing
                """,
                (user_id, email),
            )


def create_pool(settings: Settings) -> AsyncConnectionPool[Any]:
    return AsyncConnectionPool(
        conninfo=str(settings.database_url),
        min_size=settings.database_pool_min_size,
        max_size=settings.database_pool_max_size,
        timeout=settings.database_pool_timeout_seconds,
        open=False,
        check=AsyncConnectionPool.check_connection,
        max_lifetime=1800,
        kwargs={"row_factory": dict_row},
    )


async def get_bootstrap_store(request: Request) -> BootstrapStore:
    settings: Settings = request.app.state.settings
    return PostgresBootstrapStore(request.app.state.pool, settings.database_pool_timeout_seconds)


async def database_is_ready(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    try:
        async with (
            request.app.state.pool.connection(
                timeout=settings.database_pool_timeout_seconds
            ) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                "select to_regclass('app.jobs_profile') is not null"
            )
            row = await cursor.fetchone()
            return bool(row and next(iter(row.values())))
    except Exception:
        return False


@asynccontextmanager
async def opened_pool(pool: AsyncConnectionPool[Any]) -> AsyncIterator[None]:
    await pool.open(wait=True)
    try:
        yield
    finally:
        await pool.close()
