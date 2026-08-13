from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from personal_os.auth import InvalidAccessToken
from personal_os.config import Settings
from personal_os.database import BootstrapStore, get_bootstrap_store
from personal_os.jobs_models import JobListingPage
from personal_os.jobs_store import get_jobs_store
from personal_os.main import create_app
from personal_os.models import Principal

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")


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

    async def list_listings(self, user_id: UUID, **filters: object) -> JobListingPage:
        self.filters = filters
        return JobListingPage(items=[], total=0, limit=200, offset=0)


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
    response = await client.get(
        "/v1/me", headers={"Authorization": "Bearer wrong-account"}
    )
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
            ("limit", "200"),
        ],
        headers={"Authorization": "Bearer approved"},
    )

    assert response.status_code == 200
    assert store.filters == {
        "category": "quant",
        "locations": ["Remote", "New York City, NY"],
        "limit": 200,
        "offset": 0,
    }
