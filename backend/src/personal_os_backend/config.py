"""Settings from the environment, and the database pool, its readiness check and StoreBase."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Self
from uuid import UUID

from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import AnyHttpUrl, Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the development .env from the package rather than the process working
# directory, so the API starts the same way from any directory. The relative
# entry is kept first so an explicitly placed .env still wins, and a missing file
# is simply ignored, which is how the container runs.
_PACKAGE_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PERSONAL_OS_API_",
        env_file=(".env", _PACKAGE_ENV_FILE),
        extra="forbid",
    )

    database_url: PostgresDsn
    supabase_url: AnyHttpUrl
    allowed_user_id: UUID
    allowed_email: str
    jwt_audience: str = "authenticated"
    jwks_cache_seconds: int = Field(default=600, ge=1, le=3600)
    database_pool_min_size: int = Field(default=1, ge=0)
    database_pool_max_size: int = Field(default=4, ge=1)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    cors_allowed_origins: list[str] = Field(default_factory=list)
    supabase_secret_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    @field_validator("allowed_email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized or "@" not in normalized:
            raise ValueError("allowed_email must be a valid email address")
        return normalized

    @model_validator(mode="after")
    def validate_pool(self) -> Self:
        if self.database_pool_min_size > self.database_pool_max_size:
            raise ValueError("database pool minimum cannot exceed maximum")
        return self

    @property
    def jwt_issuer(self) -> str:
        return f"{str(self.supabase_url).rstrip('/')}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.jwt_issuer}/.well-known/jwks.json"


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
