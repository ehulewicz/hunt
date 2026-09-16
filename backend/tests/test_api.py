from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from personal_os.auth import InvalidAccessToken
from personal_os.config import Settings
from personal_os.database import BootstrapStore, get_bootstrap_store
from personal_os.jobs_models import (
    AnswerLibraryCreate,
    AnswerLibraryItem,
    ApplicationUpdate,
    JobApplication,
    JobListing,
    JobListingPage,
    ListingSyncResponse,
)
from personal_os.jobs_sources import SpeedyApplySource
from personal_os.jobs_store import get_jobs_store
from personal_os.main import create_app
from personal_os.models import Principal

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
APPLICATION_ID = UUID("11111111-1111-4111-8111-111111111111")
POSTING_ID = UUID("22222222-2222-4222-8222-222222222222")
ANSWER_ID = UUID("33333333-3333-4333-8333-333333333333")


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeValidator:
    async def verify(self, token: str) -> Principal:
        if token == "approved":
            return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", "https://example.com/a.png")
        if token == "wrong-account":
            return Principal(
                UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                "other@gmail.com",
                "Other",
                None,
            )
        raise InvalidAccessToken


class FakeStore(BootstrapStore):
    def __init__(self) -> None:
        self.rows: set[tuple[UUID, str]] = set()
        self.calls = 0

    async def bootstrap(self, user_id: UUID, email: str) -> None:
        self.calls += 1
        self.rows.add((user_id, email))


class FakeJobsStore:
    def __init__(self) -> None:
        self.filters: dict[str, object] = {}
        self.archived_application_id: UUID | None = None
        self.created_application: tuple[UUID, str] | None = None
        self.application_update: tuple[UUID, ApplicationUpdate] | None = None
        self.answer_create: AnswerLibraryCreate | None = None
        self.answer_update: tuple[UUID, object] | None = None
        self.synced_count = 0

    async def list_listings(self, user_id: UUID, **filters: object) -> JobListingPage:
        self.filters = filters
        return JobListingPage(items=[], total=0, limit=200, offset=0)

    async def get_listing(self, user_id: UUID, reference: str) -> JobListing | None:
        from personal_os.jobs_store import AmbiguousReference

        if reference == "1111":
            raise AmbiguousReference(reference)
        if reference in {"3", "22222222"}:
            return JobListing(
                id=POSTING_ID,
                company_name="Acme",
                title="Backend Engineer Intern",
                location=None,
                salary_text=None,
                application_url="https://boards.example/jobs/1",
                discovery_url=None,
                role_families=["software_engineering"],
                source_category="other",
                normalized_locations=[],
                career_stage="internship",
                feed_region="usa",
                source_posted_at=None,
                source_age_days=0,
                status="saved",
                row=3 if reference == "3" else None,
            )
        return None

    async def sync_listings(self, user_id: UUID, listings: object) -> ListingSyncResponse:
        self.synced_count = len(listings)  # type: ignore[arg-type]
        return ListingSyncResponse(
            source="speedyapply/college-jobs",
            seen=self.synced_count,
            open=self.synced_count,
            synced_at=datetime(2026, 8, 21, tzinfo=UTC),
        )

    async def archive_application(self, user_id: UUID, application_id: UUID) -> bool:
        self.archived_application_id = application_id
        return application_id == APPLICATION_ID

    async def create_application(
        self, user_id: UUID, posting_id: UUID, status: str
    ) -> JobApplication | None:
        self.created_application = (posting_id, status)
        return (await self.list_applications(user_id))[0] if posting_id == POSTING_ID else None

    async def list_applications(self, user_id: UUID) -> list[JobApplication]:
        current_status = self.created_application[1] if self.created_application else "applying"
        return [
            JobApplication(
                id=APPLICATION_ID,
                posting_id=POSTING_ID,
                company_name="Acme",
                title="Backend Engineer Intern",
                location=None,
                application_url="https://boards.example/jobs/1",
                status=current_status,
                applied_at=None,
                next_action=None,
                next_action_at=None,
                notes=None,
                created_at=datetime(2026, 9, 14, tzinfo=UTC),
                updated_at=datetime(2026, 9, 14, tzinfo=UTC),
                unanswered_required_questions=[],
            )
        ]

    async def update_application(
        self, user_id: UUID, application_id: UUID, update: ApplicationUpdate
    ) -> JobApplication | None:
        self.application_update = (application_id, update)
        return (await self.list_applications(user_id))[0]

    async def create_answer(self, user_id: UUID, item: AnswerLibraryCreate) -> AnswerLibraryItem:
        self.answer_create = item
        return AnswerLibraryItem(
            id=ANSWER_ID,
            question="Why this team?",
            normalized_key="why this team",
            answer_text="I want to build reliable developer tools.",
            reuse_policy="exact",
            approved=True,
            updated_at=datetime(2026, 8, 13, tzinfo=UTC),
        )

    async def update_answer(
        self, user_id: UUID, answer_id: UUID, item: object
    ) -> AnswerLibraryItem | None:
        self.answer_update = (answer_id, item)
        if answer_id != ANSWER_ID:
            return None
        return AnswerLibraryItem(
            id=ANSWER_ID,
            question="Why this team?",
            normalized_key="why this team",
            answer_text="I want to build dependable developer tools.",
            reuse_policy="exact",
            approved=False,
            updated_at=datetime(2026, 8, 13, tzinfo=UTC),
        )


@pytest.fixture
def app() -> FastAPI:
    settings = Settings(
        database_url="postgresql://postgres:password@localhost:5432/postgres",
        supabase_url="https://exampleprojectref.supabase.co",
        allowed_user_id=USER_ID,
        allowed_email="eahtree@gmail.com",
    )
    application = create_app(settings, lifespan=no_lifespan)
    application.state.jwt_validator = FakeValidator()
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api_client:
        yield api_client


async def test_me_requires_a_bearer_token(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/me")
    assert response.status_code == 401


async def test_invalid_token_is_unauthorized(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/me", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401


async def test_valid_but_unapproved_account_is_forbidden(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/me", headers={"Authorization": "Bearer wrong-account"})
    assert response.status_code == 403


async def test_me_returns_google_identity(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/me", headers={"Authorization": "Bearer approved"})
    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(USER_ID),
        "email": "eahtree@gmail.com",
        "display_name": "Eah Tree",
        "avatar_url": "https://example.com/a.png",
    }


async def test_bootstrap_is_safe_to_repeat(app: FastAPI, client: httpx.AsyncClient) -> None:
    store = FakeStore()

    async def override_store() -> FakeStore:
        return store

    app.dependency_overrides[get_bootstrap_store] = override_store
    headers = {"Authorization": "Bearer approved"}

    first = await client.post("/v1/bootstrap", headers=headers)
    second = await client.post("/v1/bootstrap", headers=headers)

    assert first.status_code == second.status_code == 200
    assert store.rows == {(USER_ID, "eahtree@gmail.com")}
    assert store.calls == 2


async def test_listings_accept_category_and_multiple_normalized_locations(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.get(
        "/v1/jobs/listings",
        params=[
            ("category", "quant"),
            ("locations", "Remote"),
            ("locations", "New York City, NY"),
            ("role_families", "software_engineering"),
            ("role_families", "ml_engineering"),
            ("limit", "500"),
        ],
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 200
    assert store.filters == {
        "category": "quant",
        "locations": ["Remote", "New York City, NY"],
        "role_families": ["software_engineering", "ml_engineering"],
        "limit": 500,
        "offset": 0,
    }


async def test_listings_reject_a_non_positive_limit(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.get(
        "/v1/jobs/listings",
        params={"limit": 0},
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 422
    assert store.filters == {}


async def test_listing_sync_persists_the_complete_deterministic_source(
    app: FastAPI,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    async def fake_fetch(self: SpeedyApplySource) -> list[object]:
        return [object(), object()]

    app.dependency_overrides[get_jobs_store] = override_store
    monkeypatch.setattr(SpeedyApplySource, "fetch", fake_fetch)
    response = await client.post(
        "/v1/jobs/listings/sync",
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 200
    assert response.json()["seen"] == 2
    assert store.synced_count == 2


async def test_application_delete_archives_through_the_jobs_store(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.delete(
        f"/v1/jobs/applications/{APPLICATION_ID}",
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 204
    assert store.archived_application_id == APPLICATION_ID


async def test_application_patch_cannot_bypass_archival_cleanup(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.patch(
        f"/v1/jobs/applications/{APPLICATION_ID}",
        json={"status": "archived"},
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 422
    assert store.archived_application_id is None


async def test_a_listing_resolves_by_board_number_or_id_prefix(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    headers = {"Authorization": "Bearer approved"}
    by_row = await client.get("/v1/jobs/listings/3", headers=headers)
    assert by_row.status_code == 200 and by_row.json()["row"] == 3
    by_prefix = await client.get("/v1/jobs/listings/22222222", headers=headers)
    assert by_prefix.status_code == 200 and by_prefix.json()["id"] == str(POSTING_ID)
    assert (await client.get("/v1/jobs/listings/1111", headers=headers)).status_code == 409
    assert (await client.get("/v1/jobs/listings/99", headers=headers)).status_code == 404


async def test_an_application_can_be_recorded_without_a_queue(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.post(
        "/v1/jobs/applications",
        headers={"Authorization": "Bearer approved"},
        json={"posting_id": str(POSTING_ID), "status": "applied"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "applied"
    assert store.created_application == (POSTING_ID, "applied")

    retired = await client.post(
        "/v1/jobs/applications/queue",
        headers={"Authorization": "Bearer approved"},
        json={"posting_ids": [str(uuid4())]},
    )
    assert retired.status_code == 405


async def test_application_status_is_the_new_lifecycle_only(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    for status in (
        "applying", "applied", "oa", "oa_completed", "interview", "offer", "rejected", "skipped"
    ):
        response = await client.patch(
            f"/v1/jobs/applications/{APPLICATION_ID}",
            headers={"Authorization": "Bearer approved"},
            json={"status": status},
        )
        assert response.status_code == 200, status
    for retired in ("queued", "preparing", "ready", "submitted", "interviewing", "withdrawn"):
        response = await client.patch(
            f"/v1/jobs/applications/{APPLICATION_ID}",
            headers={"Authorization": "Bearer approved"},
            json={"status": retired},
        )
        assert response.status_code == 422, retired


async def test_long_form_answers_are_created_without_a_client_key(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.post(
        "/v1/jobs/answers",
        json={
            "question": "Why this team?",
            "answer_text": "I want to build reliable developer tools.",
            "approved": True,
        },
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 201
    assert response.json()["id"] == str(ANSWER_ID)
    assert "key" not in response.json()
    assert store.answer_create is not None
    assert store.answer_create.question == "Why this team?"
    assert store.answer_create.reuse_policy == "exact"


async def test_long_form_answers_are_updated_by_id(app: FastAPI, client: httpx.AsyncClient) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.patch(
        f"/v1/jobs/answers/{ANSWER_ID}",
        json={"answer_text": "I want to build dependable developer tools.", "approved": False},
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 200
    assert response.json()["approved"] is False
    assert store.answer_update is not None and store.answer_update[0] == ANSWER_ID


async def test_long_form_answer_creation_requires_explicit_approval(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.post(
        "/v1/jobs/answers",
        json={"question": "Why this team?", "answer_text": "A thoughtful response."},
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 422
    assert store.answer_create is None


@pytest.mark.parametrize(
    "update",
    [
        {"question": None},
        {"answer_text": None},
        {"approved": None},
    ],
)
async def test_long_form_answer_updates_reject_explicit_nulls(
    app: FastAPI, client: httpx.AsyncClient, update: dict[str, object]
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.patch(
        f"/v1/jobs/answers/{ANSWER_ID}",
        json=update,
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 422
    assert store.answer_update is None


async def test_unhandled_errors_stay_visible_to_the_browser() -> None:
    """A 500 without CORS headers reaches the browser as "Failed to fetch".

    Starlette hands an `Exception` handler to ServerErrorMiddleware, which sits
    above CORSMiddleware, so the catch must live in middleware added before it.
    """
    settings = Settings(
        database_url="postgresql://postgres:password@localhost:5432/postgres",
        supabase_url="https://exampleprojectref.supabase.co",
        allowed_user_id=USER_ID,
        allowed_email="eahtree@gmail.com",
        cors_allowed_origins=["https://personal-os-ten-kappa.vercel.app"],
    )
    application = create_app(settings, lifespan=no_lifespan)

    @application.get("/boom-for-test")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as api_client:
        response = await api_client.get(
            "/boom-for-test",
            headers={"Origin": "https://personal-os-ten-kappa.vercel.app"},
        )

    assert response.status_code == 500
    assert (
        response.headers.get("access-control-allow-origin")
        == "https://personal-os-ten-kappa.vercel.app"
    )
    assert response.json() == {"detail": "Internal server error"}


async def test_answer_review_uses_verified_owner_and_bounds(
    app: FastAPI, client: httpx.AsyncClient,
) -> None:
    calls: list[tuple[UUID, int, int]] = []

    class ReviewStore:
        async def list_answer_review(
            self, user_id: UUID, *, limit: int, offset: int,
        ) -> list[object]:
            calls.append((user_id, limit, offset))
            return []

    async def override_review() -> ReviewStore:
        return ReviewStore()

    app.dependency_overrides[get_jobs_store] = override_review
    headers = {"Authorization": "Bearer approved"}
    response = await client.get("/v1/jobs/answer-review?limit=20&offset=5", headers=headers)
    assert response.status_code == 200
    assert response.json() == []
    assert calls == [(USER_ID, 20, 5)]
    assert (await client.get("/v1/jobs/answer-review")).status_code == 401
    assert (await client.get(
        "/v1/jobs/answer-review", headers={"Authorization": "Bearer wrong-account"}
    )).status_code == 403
    for query in ("limit=201", "limit=0", "offset=-1"):
        response = await client.get(f"/v1/jobs/answer-review?{query}", headers=headers)
        assert response.status_code == 422
    assert len(calls) == 1
