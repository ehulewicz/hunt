from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from personal_os_backend.auth import Principal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_gaps import (
    PostgresGapStore,
    ProseGapWrite,
    application_prose,
    get_gap_store,
)
from personal_os_backend.main import create_app

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
HEADERS = {"Authorization": "Bearer approved"}
APPLICATION_ID = UUID("44444444-4444-4444-8444-444444444444")
QUESTION_ID = UUID("55555555-5555-4555-8555-555555555555")

NOW = "2026-10-30T12:00:00Z"


def gap(**overrides: Any) -> dict[str, Any]:
    return {
        "question": "Tell us about a project you are proud of.",
        "application_id": None,
        "family": "proudest_work",
        "coverage": "missing",
        "missing_detail": "",
        **overrides,
    }


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeValidator:
    async def verify(self, token: str) -> Principal:
        return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", None)


class GapConnection:
    """Emulates the question upsert, the one-open-gap upsert, and the reads."""

    def __init__(self) -> None:
        self.questions: dict[str, UUID] = {}
        self.labels: dict[UUID, str] = {}
        self.gaps: dict[UUID, dict[str, Any]] = {}
        self.applications: set[UUID] = {APPLICATION_ID}
        self._one: dict[str, Any] | None = None
        self._many: list[dict[str, Any]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["GapConnection"]:
        yield self

    def _row(self, gap: dict[str, Any]) -> dict[str, Any]:
        return {
            **gap,
            "question": self.labels.get(gap["question_id"]),
            "company_name": "Acme" if gap["application_id"] else None,
            "title": "Backend Intern" if gap["application_id"] else None,
        }

    async def execute(self, query: str, params: Any = None) -> "GapConnection":
        self._one = None
        self._many = []
        if "insert into app.jobs_questions" in query:
            key = params[1]
            self.questions.setdefault(key, uuid4())
            self.labels[self.questions[key]] = params[2]
            self._one = {"id": self.questions[key]}
        elif "insert into app.jobs_prose_gaps" in query:
            _user, question_id, application_id, _user2, family, coverage, detail = params
            application = application_id if application_id in self.applications else None
            open_id = next(
                (
                    gap_id
                    for gap_id, row in self.gaps.items()
                    if row["question_id"] == question_id and row["resolved_at"] is None
                ),
                None,
            )
            if open_id is None:
                open_id = uuid4()
                self.gaps[open_id] = {
                    "id": open_id, "question_id": question_id,
                    "created_at": NOW, "resolved_at": None,
                }
            self.gaps[open_id].update(
                application_id=(
                    application
                    if application is not None
                    else self.gaps[open_id].get("application_id")
                ),
                family=family, coverage=coverage, missing_detail=detail,
            )
            self._one = {"id": open_id}
        elif "update app.jobs_prose_gaps set resolved_at" in query:
            row = self.gaps.get(params[0])
            if row is not None and row["resolved_at"] is None:
                row["resolved_at"] = NOW
                self._one = {"id": row["id"]}
        elif "from app.jobs_prose_gaps" in query and "where gap.id = %s" in query:
            row = self.gaps.get(params[0])
            self._one = self._row(row) if row else None
        elif "from app.jobs_prose_gaps" in query:
            rows = list(self.gaps.values())
            if params and len(params) > 1 and params[1] is True:
                rows = [row for row in rows if row["resolved_at"] is None]
            self._many = [self._row(row) for row in rows]
        return self

    async def fetchone(self) -> dict[str, Any] | None:
        return self._one

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._many


class GapPool:
    def __init__(self, connection: GapConnection) -> None:
        self.gaps = connection

    @asynccontextmanager
    async def connection(self, **_: Any) -> AsyncIterator[GapConnection]:
        yield self.gaps


@pytest.fixture
def connection() -> GapConnection:
    return GapConnection()


@pytest.fixture
def store(connection: GapConnection) -> PostgresGapStore:
    return PostgresGapStore(GapPool(connection), 5)  # type: ignore[arg-type]


@pytest.fixture
def client(store: PostgresGapStore) -> httpx.AsyncClient:
    app = create_app(
        Settings(
            database_url="postgresql://postgres:password@localhost:5432/postgres",
            supabase_url="https://exampleprojectref.supabase.co",
            allowed_user_id=USER_ID,
            allowed_email="eahtree@gmail.com",
        ),
        lifespan=no_lifespan,
    )
    app.state.jwt_validator = FakeValidator()

    async def override() -> PostgresGapStore:
        return store

    app.dependency_overrides[get_gap_store] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_one_open_gap_per_question_and_resolve_frees_the_next(
    client: httpx.AsyncClient, connection: GapConnection
) -> None:
    async with client:
        first = await client.post("/v1/jobs/prose/gaps", headers=HEADERS, json=gap())
        assert first.status_code == 201
        first_id = first.json()["id"]

        # A second POST for the same question updates the open gap, not adds one.
        second = await client.post(
            "/v1/jobs/prose/gaps",
            headers=HEADERS,
            json=gap(coverage="partial", missing_detail="no numbers yet",
                     application_id=str(APPLICATION_ID)),
        )
        assert second.status_code == 201
        assert second.json()["id"] == first_id
        assert second.json()["coverage"] == "partial"
        assert second.json()["application_id"] == str(APPLICATION_ID)
        assert second.json()["company_name"] == "Acme"
        assert len(connection.gaps) == 1

        resolved = await client.post(
            f"/v1/jobs/prose/gaps/{first_id}/resolve", headers=HEADERS
        )
        assert resolved.status_code == 200
        assert resolved.json()["resolved_at"] is not None
        assert (
            await client.post(f"/v1/jobs/prose/gaps/{first_id}/resolve", headers=HEADERS)
        ).status_code == 404

        third = await client.post("/v1/jobs/prose/gaps", headers=HEADERS, json=gap())
        assert third.json()["id"] != first_id

        all_gaps = await client.get("/v1/jobs/prose/gaps", headers=HEADERS)
        assert len(all_gaps.json()) == 2
        open_gaps = await client.get("/v1/jobs/prose/gaps?open=true", headers=HEADERS)
        assert [row["id"] for row in open_gaps.json()] == [third.json()["id"]]


async def test_a_label_that_asks_nothing_is_refused(client: httpx.AsyncClient) -> None:
    async with client:
        refused = await client.post(
            "/v1/jobs/prose/gaps", headers=HEADERS, json=gap(question="Yes")
        )
        assert refused.status_code == 422
        assert "asks nothing" in refused.json()["detail"]


async def test_another_users_application_is_dropped(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        created = await client.post(
            "/v1/jobs/prose/gaps",
            headers=HEADERS,
            json=gap(application_id=str(uuid4())),
        )
        assert created.status_code == 201
        assert created.json()["application_id"] is None


async def test_family_is_a_fixed_vocabulary(client: httpx.AsyncClient) -> None:
    async with client:
        refused = await client.post(
            "/v1/jobs/prose/gaps", headers=HEADERS, json=gap(family="nonsense")
        )
        assert refused.status_code == 422


class CaptureConnection:
    def __init__(self) -> None:
        self.query: str | None = None
        self.params: Any = None

    async def execute(self, query: str, params: Any = None) -> "CaptureConnection":
        self.query = query
        self.params = params
        return self

    async def fetchall(self) -> list[dict[str, Any]]:
        return []


async def test_run_prose_is_scoped_to_its_application() -> None:
    connection = CaptureConnection()
    result = await application_prose(connection, USER_ID, APPLICATION_ID)  # type: ignore[arg-type]
    assert result == []
    assert connection.params == (USER_ID, APPLICATION_ID)
    assert connection.query is not None
    assert "prose.application_id = %s" in connection.query
    # Approved or not: there is no approved filter on the rows it reads.
    assert "and prose.approved" not in connection.query


def test_write_model_bounds() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProseGapWrite.model_validate(gap(question=""))


async def test_a_gap_post_without_an_application_keeps_the_known_one(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        first = await client.post(
            "/v1/jobs/prose/gaps",
            headers=HEADERS,
            json=gap(application_id=str(APPLICATION_ID)),
        )
        assert first.json()["application_id"] == str(APPLICATION_ID)
        second = await client.post("/v1/jobs/prose/gaps", headers=HEADERS, json=gap())
        assert second.json()["application_id"] == str(APPLICATION_ID)
