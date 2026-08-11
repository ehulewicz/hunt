from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials

from personal_os.auth import bearer
from personal_os.config import Settings
from personal_os.jobs_store import JobsStore, get_jobs_store


@dataclass(frozen=True, slots=True)
class WorkerPrincipal:
    user_id: UUID
    email: str
    credential_id: UUID


async def authenticated_worker(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    store: Annotated[JobsStore, Depends(get_jobs_store)],
) -> WorkerPrincipal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Worker authentication required")
    settings: Settings = request.app.state.settings
    secret = settings.worker_jwt_secret
    if secret is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Worker authentication unavailable"
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            secret.get_secret_value(),
            algorithms=["HS256"],
            audience=settings.worker_jwt_audience,
            issuer=settings.worker_jwt_issuer,
            options={"require": ["sub", "email", "jti", "scope", "exp", "iat"]},
        )
        user_id = UUID(str(payload["sub"]))
        credential_id = UUID(str(payload["jti"]))
        email = str(payload["email"]).strip().casefold()
        if payload["scope"] != "jobs:worker":
            raise ValueError("Invalid scope")
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid worker token") from None
    if user_id != settings.allowed_user_id or email != settings.allowed_email:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Worker account is not authorized")
    if not await store.validate_worker_credential(
        user_id, credential_id, credentials.credentials
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Worker token is expired or revoked")
    return WorkerPrincipal(user_id=user_id, email=email, credential_id=credential_id)


CurrentWorker = Annotated[WorkerPrincipal, Depends(authenticated_worker)]
