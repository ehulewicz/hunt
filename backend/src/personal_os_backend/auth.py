from dataclasses import dataclass
from time import monotonic
from typing import Annotated, Any, Protocol, cast
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from personal_os_backend.config import Settings

ALLOWED_ALGORITHMS = frozenset({"RS256", "ES256", "EdDSA"})
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    email: str
    display_name: str
    avatar_url: str | None


class AccountResponse(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    avatar_url: str | None

    @classmethod
    def from_principal(cls, principal: Principal) -> "AccountResponse":
        return cls(
            user_id=principal.user_id,
            email=principal.email,
            display_name=principal.display_name,
            avatar_url=principal.avatar_url,
        )


class HealthResponse(BaseModel):
    status: str


class InvalidAccessToken(Exception):
    """Raised when a bearer token cannot be trusted."""


class JwtValidator:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client
        self._jwks: list[dict[str, Any]] = []
        self._jwks_expires_at = 0.0

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            key_id = header.get("kid")
            if algorithm not in ALLOWED_ALGORITHMS or not isinstance(key_id, str):
                raise InvalidAccessToken

            jwk = await self._find_jwk(key_id, force_refresh=False)
            if jwk is None:
                jwk = await self._find_jwk(key_id, force_refresh=True)
            if jwk is None or jwk.get("alg") not in (None, algorithm):
                raise InvalidAccessToken

            signing_key = jwt.PyJWK.from_dict(  # type: ignore[attr-defined]
                jwk, algorithm=algorithm
            ).key
            payload = jwt.decode(
                token,
                key=signing_key,
                algorithms=[algorithm],
                audience=self._settings.jwt_audience,
                issuer=self._settings.jwt_issuer,
                options={"require": ["sub", "iss", "aud", "exp", "email"]},
            )
            return self._principal_from_payload(dict(payload))
        except (InvalidAccessToken, jwt.PyJWTError, httpx.HTTPError, TypeError, ValueError):
            raise InvalidAccessToken from None

    async def _find_jwk(self, key_id: str, *, force_refresh: bool) -> dict[str, Any] | None:
        if force_refresh or monotonic() >= self._jwks_expires_at:
            response = await self._client.get(self._settings.jwks_url)
            response.raise_for_status()
            keys = response.json().get("keys")
            if not isinstance(keys, list):
                raise InvalidAccessToken
            self._jwks = [key for key in keys if isinstance(key, dict)]
            self._jwks_expires_at = monotonic() + self._settings.jwks_cache_seconds
        return next((key for key in self._jwks if key.get("kid") == key_id), None)

    @staticmethod
    def _principal_from_payload(payload: dict[str, Any]) -> Principal:
        try:
            user_id = UUID(str(payload["sub"]))
        except (KeyError, TypeError, ValueError):
            raise InvalidAccessToken from None
        email = payload.get("email")
        if not isinstance(email, str) or not email.strip():
            raise InvalidAccessToken
        metadata_value = payload.get("user_metadata")
        metadata = metadata_value if isinstance(metadata_value, dict) else {}
        display_name = next(
            (
                value.strip()
                for value in (metadata.get("full_name"), metadata.get("name"))
                if isinstance(value, str) and value.strip()
            ),
            email.partition("@")[0],
        )
        avatar_value = metadata.get("avatar_url", metadata.get("picture"))
        avatar_url = avatar_value if isinstance(avatar_value, str) else None
        return Principal(user_id, email.strip(), display_name, avatar_url)


async def authenticated_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    try:
        validator = cast(TokenValidator, request.app.state.jwt_validator)
        principal = await validator.verify(credentials.credentials)
    except InvalidAccessToken:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid access token") from None

    settings: Settings = request.app.state.settings
    if (
        principal.user_id != settings.allowed_user_id
        or principal.email.casefold() != settings.allowed_email
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is not authorized")
    return principal


CurrentPrincipal = Annotated[Principal, Depends(authenticated_principal)]


class TokenValidator(Protocol):
    async def verify(self, token: str) -> Principal: ...
