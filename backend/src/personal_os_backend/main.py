import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from personal_os_backend.auth import CurrentPrincipal, JwtValidator
from personal_os_backend.config import Settings
from personal_os_backend.database import (
    BootstrapStore,
    create_pool,
    database_is_ready,
    get_bootstrap_store,
)
from personal_os_backend.jobs_router import router as jobs_router
from personal_os_backend.models import AccountResponse, HealthResponse

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


class UnhandledErrorMiddleware(BaseHTTPMiddleware):
    """Turn an unhandled exception into a JSON 500 that still carries CORS headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except Exception:
            logging.getLogger(__name__).exception(
                "Unhandled error for %s %s", request.method, request.url.path
            )
            return JSONResponse({"detail": "Internal server error"}, status_code=500)


@asynccontextmanager
async def application_lifespan(app: FastAPI) -> AsyncIterator[None]:
    await app.state.pool.open(wait=True)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            app.state.jwt_validator = JwtValidator(app.state.settings, client)
            yield
    finally:
        await app.state.pool.close()


def create_app(settings: Settings | None = None, *, lifespan: Lifespan | None = None) -> FastAPI:
    resolved = settings or Settings()
    application = FastAPI(
        title="Personal OS API",
        version="0.1.0",
        lifespan=lifespan or application_lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.settings = resolved
    application.state.pool = create_pool(resolved)

    # Added before CORSMiddleware so it ends up inside it. Starlette hands an
    # `Exception` handler to ServerErrorMiddleware, which sits above CORS, so the
    # browser would see a header-less response and report "Failed to fetch"
    # instead of a status. Catching here keeps the 500 inside the CORS layer.
    application.add_middleware(UnhandledErrorMiddleware)
    if resolved.cors_allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @application.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        if not await database_is_ready(request):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Service unavailable")
        return HealthResponse(status="ok")

    @application.get("/v1/me", response_model=AccountResponse)
    async def me(principal: CurrentPrincipal) -> AccountResponse:
        return AccountResponse.from_principal(principal)

    @application.post("/v1/bootstrap", response_model=AccountResponse)
    async def bootstrap(
        principal: CurrentPrincipal,
        store: Annotated[BootstrapStore, Depends(get_bootstrap_store)],
    ) -> AccountResponse:
        await store.bootstrap(principal.user_id, principal.email)
        return AccountResponse.from_principal(principal)

    application.include_router(jobs_router)

    return application


app = create_app()
