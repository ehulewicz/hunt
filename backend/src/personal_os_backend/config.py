from pathlib import Path
from typing import Self
from uuid import UUID

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
