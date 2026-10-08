from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from personal_os_backend.jobs_models import ListingSyncResponse
from personal_os_backend.jobs_sources import SourceListing
from personal_os_backend.jobs_sync import run_sync


class FakeSource:
    def __init__(self, listings: list[SourceListing]) -> None:
        self.listings = listings

    async def fetch(self) -> list[SourceListing]:
        return self.listings


class FakeStore:
    user_id: UUID | None = None

    async def sync_listings(
        self, user_id: UUID, listings: list[SourceListing]
    ) -> ListingSyncResponse:
        self.user_id = user_id
        assert len(listings) == 1
        return ListingSyncResponse(
            source="speedyapply/college-jobs",
            seen=0,
            open=0,
            synced_at=datetime(2026, 8, 13, tzinfo=UTC),
        )


async def test_run_sync_uses_the_configured_single_user() -> None:
    user_id = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
    store = FakeStore()
    listing = SourceListing(
        company="Example",
        company_url=None,
        title="Software Engineering Intern",
        location="Remote",
        salary_text=None,
        application_url="https://example.com/jobs/1",
        discovery_url="https://github.com/speedyapply/2027-SWE-College-Jobs",
        external_id="1",
        role_families=("software_engineering",),
        category="other",
        normalized_locations=("Remote",),
        career_stage="internship",
        feed_region="usa",
        source_posted_at=date(2026, 8, 13),
        source_age_days=0,
    )

    result = await run_sync(user_id, FakeSource([listing]), store)

    assert store.user_id == user_id
    assert result.source == "speedyapply/college-jobs"


async def test_run_sync_rejects_an_empty_parse_before_mutating_the_database() -> None:
    with pytest.raises(RuntimeError, match="returned no listings"):
        await run_sync(
            UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f"),
            FakeSource([]),
            FakeStore(),
        )
