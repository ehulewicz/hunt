from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from personal_os.auth import InvalidAccessToken
from personal_os.config import Settings
from personal_os.database import BootstrapStore, get_bootstrap_store
from personal_os.jobs_models import (
    AnswerLibraryCreate,
    AnswerLibraryItem,
    JobListingPage,
    ListingSyncResponse,
)
from personal_os.jobs_sources import SpeedyApplySource
from personal_os.jobs_store import get_jobs_store
from personal_os.main import create_app
from personal_os.models import Principal

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
APPLICATION_ID = UUID("11111111-1111-4111-8111-111111111111")
TASK_ID = UUID("22222222-2222-4222-8222-222222222222")
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
        self.autofill_application_id: UUID | None = None
        self.answer_create: AnswerLibraryCreate | None = None
        self.answer_update: tuple[UUID, object] | None = None
        self.synced_count = 0

    async def list_listings(self, user_id: UUID, **filters: object) -> JobListingPage:
        self.filters = filters
        return JobListingPage(items=[], total=0, limit=200, offset=0)

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

    async def queue_autofill(self, user_id: UUID, application_id: UUID) -> UUID | None:
        self.autofill_application_id = application_id
        return TASK_ID if application_id == APPLICATION_ID else None

    async def create_answer(self, user_id: UUID, item: AnswerLibraryCreate) -> AnswerLibraryItem:
        self.answer_create = item
        return AnswerLibraryItem(
            id=ANSWER_ID,
            question="Why this team?",
            answer_text="I want to build reliable developer tools.",
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
            answer_text="I want to build dependable developer tools.",
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
            ("limit", "300"),
        ],
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 200
    assert store.filters == {
        "category": "quant",
        "locations": ["Remote", "New York City, NY"],
        "role_families": ["software_engineering", "ml_engineering"],
        "limit": 300,
        "offset": 0,
    }


async def test_listings_reject_a_limit_above_300(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.get(
        "/v1/jobs/listings",
        params={"limit": 301},
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


async def test_application_autofill_retry_returns_the_queued_task(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    store = FakeJobsStore()

    async def override_store() -> FakeJobsStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    response = await client.post(
        f"/v1/jobs/applications/{APPLICATION_ID}/autofill",
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 202
    assert response.json() == {"task_id": str(TASK_ID), "status": "queued"}
    assert store.autofill_application_id == APPLICATION_ID


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
