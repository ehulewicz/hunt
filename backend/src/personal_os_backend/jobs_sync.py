from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

import httpx

from personal_os_backend.config import Settings
from personal_os_backend.database import create_pool, opened_pool
from personal_os_backend.jobs_models import ListingSyncResponse
from personal_os_backend.jobs_sources import SourceListing, SpeedyApplySource
from personal_os_backend.jobs_store import PostgresJobsStore


class ListingSource(Protocol):
    async def fetch(self) -> Sequence[SourceListing]: ...


class ListingStore(Protocol):
    async def sync_listings(
        self, user_id: UUID, listings: Sequence[SourceListing]
    ) -> ListingSyncResponse: ...


async def run_sync(
    user_id: UUID, source: ListingSource, store: ListingStore
) -> ListingSyncResponse:
    listings = await source.fetch()
    if not listings:
        raise RuntimeError("SpeedyApply returned no listings; refusing to close existing rows")
    return await store.sync_listings(user_id, listings)


async def sync_hosted_listings(settings: Settings) -> ListingSyncResponse:
    pool = create_pool(settings)
    async with (
        opened_pool(pool),
        httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client,
    ):
        return await run_sync(
            settings.allowed_user_id,
            SpeedyApplySource(client),
            PostgresJobsStore(pool, settings.database_pool_timeout_seconds),
        )


def main() -> None:
    result = asyncio.run(sync_hosted_listings(Settings()))
    print(
        f"Synced {result.open} open listings from {result.source} "
        f"({result.seen} rows seen)."
    )


if __name__ == "__main__":
    main()
