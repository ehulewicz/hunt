"""The Jobs store: one protocol and one Postgres store, composed from each table family's module."""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import Request
from psycopg_pool import AsyncConnectionPool

from personal_os_backend.config import Settings
from personal_os_backend.database import StoreBase
from personal_os_backend.jobs_applications import ApplicationsMixin, ApplicationsStore
from personal_os_backend.jobs_failures import (
    FailuresMixin,
    FailuresStore,
    latest_per_site,
    registrable_site,
)
from personal_os_backend.jobs_listings import (
    BOARD_QUERY,
    AmbiguousReference,
    ListingsMixin,
    ListingsStore,
)
from personal_os_backend.jobs_runs import AutofillRunsMixin, AutofillRunsStore, CompletionConflict

__all__ = [
    "BOARD_QUERY",
    "AmbiguousReference",
    "CompletionConflict",
    "JobsStore",
    "PostgresJobsStore",
    "get_jobs_store",
    "latest_per_site",
    "registrable_site",
]


class JobsStore(ListingsStore, ApplicationsStore, AutofillRunsStore, FailuresStore, Protocol): ...


class PostgresJobsStore(
    ListingsMixin, ApplicationsMixin, AutofillRunsMixin, FailuresMixin, StoreBase
):
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout


async def get_jobs_store(request: Request) -> JobsStore:
    settings: Settings = request.app.state.settings
    return PostgresJobsStore(request.app.state.pool, settings.database_pool_timeout_seconds)
