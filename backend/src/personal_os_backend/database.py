from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from personal_os_backend.config import Settings


class StoreBase:
    """The pool and timeout every store mixin shares; the composed store sets both."""

    _pool: AsyncConnectionPool[Any]
    _timeout: float


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
                "select to_regclass('app.jobs_facts') is not null"
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
