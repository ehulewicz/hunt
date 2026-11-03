from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from personal_os_backend.auth import InvalidAccessToken, Principal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_failures import latest_per_site, registrable_site
from personal_os_backend.jobs_models import AutofillSite
from personal_os_backend.jobs_router import PostgresJobsStore, get_jobs_store
from personal_os_backend.main import create_app

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")


def run(url: str | None, day: int, **fields: Any) -> dict[str, Any]:
    return {
        "id": uuid4(), "status": "review_ready", "model_calls": 0,
        "created_at": datetime(2026, 10, day, tzinfo=UTC), "url": url,
        "open_issues": 0, "unsolved_issues": 0,
    } | fields


def test_a_site_is_the_last_two_labels_of_the_host() -> None:
    assert registrable_site("https://acme.wd5.myworkdayjobs.com/en-US/x") == "myworkdayjobs.com"
    assert registrable_site("https://job-boards.greenhouse.io/acme/jobs/1") == "greenhouse.io"
    assert registrable_site("https://WWW.Amazon.Jobs./en/jobs/1") == "amazon.jobs"
    assert registrable_site("not a url") == ""


def test_each_site_keeps_its_latest_run_and_counts_the_rest() -> None:
    newest = run("https://jobs.ashbyhq.com/acme/1", 21, model_calls=0, open_issues=1)
    rows = [
        newest,
        run("https://boards.greenhouse.io/beta/2", 20, model_calls=3, unsolved_issues=2),
        run("https://jobs.ashbyhq.com/other/9", 19, model_calls=7),
        run(None, 18),
    ]

    sites = latest_per_site(rows)

    assert [site.site for site in sites] == ["ashbyhq.com", "greenhouse.io"]
    ashby = sites[0]
    assert (ashby.runs, ashby.run_id, ashby.model_calls) == (2, newest["id"], 0)
    assert (ashby.open_issues, ashby.unsolved_issues) == (1, 0)
    assert (sites[1].model_calls, sites[1].unsolved_issues) == (3, 2)


class Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, Any]] = []

    async def execute(self, query: str, params: Any = None) -> Cursor:
        self.queries.append((" ".join(query.split()), params))
        return Cursor(self.rows)


class Pool:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def connection(self, timeout: float | None = None) -> Any:
        connection = self._connection

        @asynccontextmanager
        async def context() -> AsyncIterator[Connection]:
            yield connection

        return context()


async def test_the_store_reads_runs_as_the_api_role_for_one_user() -> None:
    connection = Connection([run("https://jobs.lever.co/acme/1", 21, model_calls=2)])
    store = PostgresJobsStore(Pool(connection), 5.0)  # type: ignore[arg-type]

    sites = await store.list_autofill_sites(USER_ID)

    assert [(site.site, site.model_calls) for site in sites] == [("lever.co", 2)]
    assert connection.queries[0][0] == "set local role personal_os_api"
    query, params = connection.queries[1]
    assert params == (USER_ID,)
    assert "where run.user_id = %s" in query
    assert "coalesce(nullif(run.final_url, ''), run.application_url)" in query
    assert "occurrence.run_id = run.id" in query
    assert "issue.status in ('open', 'fixing')" in query
    assert "order by run.created_at desc, run.id" in query


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class Validator:
    async def verify(self, token: str) -> Principal:
        if token == "approved":
            return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", None)
        raise InvalidAccessToken


class SitesStore:
    def __init__(self) -> None:
        self.users: list[UUID] = []

    async def list_autofill_sites(self, user_id: UUID) -> list[AutofillSite]:
        self.users.append(user_id)
        return latest_per_site([run("https://jobs.ashbyhq.com/acme/1", 21, model_calls=0)])


@pytest.fixture
def app() -> FastAPI:
    settings = Settings(
        database_url="postgresql://postgres:password@localhost:5432/postgres",
        supabase_url="https://exampleprojectref.supabase.co",
        allowed_user_id=USER_ID,
        allowed_email="eahtree@gmail.com",
    )
    application = create_app(settings, lifespan=no_lifespan)
    application.state.jwt_validator = Validator()
    return application


async def test_sites_route_serves_the_signed_in_users_sites(app: FastAPI) -> None:
    store = SitesStore()

    async def override_store() -> SitesStore:
        return store

    app.dependency_overrides[get_jobs_store] = override_store
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        anonymous = await client.get("/v1/jobs/autofill/sites")
        response = await client.get(
            "/v1/jobs/autofill/sites",
            params={"user_id": str(uuid4())},
            headers={"Authorization": "Bearer approved"},
        )

    assert anonymous.status_code == 401
    assert response.status_code == 200
    assert store.users == [USER_ID], "the user comes from the token, never the request"
    [site] = response.json()
    assert (site["site"], site["model_calls"], site["runs"]) == ("ashbyhq.com", 0, 1)
