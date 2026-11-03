from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from personal_os_backend.auth import Principal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_library import (
    LibraryEntryWrite,
    PostgresLibraryStore,
    get_library_store,
)
from personal_os_backend.main import create_app

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
HEADERS = {"Authorization": "Bearer approved"}
ENTRY_ID = UUID("33333333-3333-4333-8333-333333333333")

NOW = "2026-10-30T12:00:00Z"


def entry(code: str = "1.1", **overrides: Any) -> dict[str, Any]:
    return {
        "code": code,
        "section": "story",
        "entry_id": None,
        "subject": None,
        "prompt": "Tell the story behind the project.",
        "guidance": "",
        "answer": None,
        "display_order": 0,
        **overrides,
    }


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeValidator:
    async def verify(self, token: str) -> Principal:
        return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", None)


class LibraryConnection:
    """Emulates `jobs_library_entries`, `jobs_resume_entries`, and `jobs_changes`.

    Every executed statement is captured so the tests can check exactly what a
    write did — most importantly that an unchanged upsert writes nothing and
    records no change.
    """

    def __init__(self, resume_ids: set[UUID] | None = None) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.changes: list[tuple[Any, ...]] = []
        self.statements: list[tuple[str, Any]] = []
        self.resume_ids = resume_ids if resume_ids is not None else {ENTRY_ID}
        self._one: dict[str, Any] | None = None
        self._many: list[dict[str, Any]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["LibraryConnection"]:
        yield self

    async def execute(self, query: str, params: Any = None) -> "LibraryConnection":
        self._one = None
        self._many = []
        if "delete from app.jobs_library_entries" in query:
            removed = self.rows.pop(params[1], None)
            self.rowcount = 1 if removed else 0
        elif "from app.jobs_library_entries" in query and "code = %s" in query:
            self._one = self.rows.get(params[1])
        elif "from app.jobs_resume_entries" in query:
            self._one = {"x": 1} if params[0] in self.resume_ids else None
        elif "update app.jobs_library_entries" in query:
            row = self.rows[params[2]]
            row["human_percent"] = params[0]
            self._one = dict(row)
        elif "insert into app.jobs_library_entries" in query:
            (
                _user, code, section, entry_id, subject, prompt, guidance, answer,
                display_order,
            ) = params
            row = self.rows.get(code)
            old_answer = row.get("answer") if row is not None else None
            if row is None:
                row = {"id": uuid4(), "code": code, "created_at": NOW}
                self.rows[code] = row
            row.update(
                section=section, entry_id=entry_id, subject=subject, prompt=prompt,
                guidance=guidance, answer=answer, display_order=display_order,
                updated_at=NOW,
            )
            # The conflict clause clears the score only when the answer changed.
            row["human_percent"] = (
                row.get("human_percent") if old_answer == answer else None
            )
            self._one = dict(row)
        elif "insert into app.jobs_changes" in query:
            self.changes.append(params)
            self._one = {
                column: value
                for column, value in zip(
                    (
                        "id", "subject", "subject_id", "action", "before", "after",
                        "origin", "run_id", "question_key", "failure_issue_id",
                        "status", "sampled", "failed", "created_at", "verified_at",
                    ),
                    (uuid4(), "library", params[2], params[3], None, None, "you",
                     None, None, None, None, None, None, NOW, None),
                    strict=True,
                )
            }
        elif "from app.jobs_library_entries" in query and "limit 1" in query:
            self._one = next(iter(self.rows.values()), None)
        elif "from app.jobs_library_entries" in query:
            self._many = sorted(
                (dict(row) for row in self.rows.values()),
                key=lambda row: (row["display_order"], row["code"]),
            )
        else:
            self.statements.append((query, params))
        return self

    async def fetchone(self) -> dict[str, Any] | None:
        return self._one

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._many


class LibraryPool:
    def __init__(self, connection: LibraryConnection) -> None:
        self.library = connection

    @asynccontextmanager
    async def connection(self, **_: Any) -> AsyncIterator[LibraryConnection]:
        yield self.library


@pytest.fixture
def connection() -> LibraryConnection:
    return LibraryConnection()


@pytest.fixture
def store(connection: LibraryConnection) -> PostgresLibraryStore:
    return PostgresLibraryStore(LibraryPool(connection), 5)  # type: ignore[arg-type]


@pytest.fixture
def client(store: PostgresLibraryStore) -> httpx.AsyncClient:
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

    async def override() -> PostgresLibraryStore:
        return store

    app.dependency_overrides[get_library_store] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_crud_and_change_recording(
    client: httpx.AsyncClient, connection: LibraryConnection
) -> None:
    async with client:
        created = await client.put(
            "/v1/jobs/library/1.1", headers=HEADERS, json=entry()
        )
        assert created.status_code == 200
        assert created.json()["code"] == "1.1"
        assert len(connection.changes) == 1
        assert connection.changes[0][3] == "create"

        # An unchanged upsert writes nothing and records nothing.
        again = await client.put("/v1/jobs/library/1.1", headers=HEADERS, json=entry())
        assert again.status_code == 200
        assert len(connection.changes) == 1

        updated = await client.put(
            "/v1/jobs/library/1.1",
            headers=HEADERS,
            json=entry(answer="The real story."),
        )
        assert updated.status_code == 200
        assert updated.json()["answer"] == "The real story."
        assert len(connection.changes) == 2 and connection.changes[1][3] == "update"

        listed = await client.get("/v1/jobs/library", headers=HEADERS)
        assert [row["code"] for row in listed.json()] == ["1.1"]

        deleted = await client.delete("/v1/jobs/library/1.1", headers=HEADERS)
        assert deleted.status_code == 204
        assert len(connection.changes) == 3 and connection.changes[2][3] == "delete"
        assert (await client.get("/v1/jobs/library", headers=HEADERS)).json() == []
        assert (
            await client.delete("/v1/jobs/library/1.1", headers=HEADERS)
        ).status_code == 404


async def test_bulk_upsert_never_deletes_what_it_omits(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        await client.put("/v1/jobs/library/1.1", headers=HEADERS, json=entry())
        await client.put("/v1/jobs/library/1.2", headers=HEADERS, json=entry("1.2"))
        bulk = await client.put(
            "/v1/jobs/library",
            headers=HEADERS,
            json=[entry("1.1", answer="rewritten"), entry("2.1", section="interest")],
        )
        assert bulk.status_code == 200
        assert {row["code"] for row in bulk.json()} == {"1.1", "1.2", "2.1"}
        # 1.2 stayed: bulk upserts never delete.
        duplicate = await client.put(
            "/v1/jobs/library", headers=HEADERS, json=[entry(), entry()]
        )
        assert duplicate.status_code == 422


async def test_human_percent_is_scored_and_cleared_by_an_answer_change(
    client: httpx.AsyncClient, connection: LibraryConnection
) -> None:
    async with client:
        await client.put("/v1/jobs/library/1.1", headers=HEADERS, json=entry())

        unscored = await client.put(
            "/v1/jobs/library/1.1/human-percent", headers=HEADERS,
            json={"percent": 92},
        )
        assert unscored.status_code == 422

        await client.put(
            "/v1/jobs/library/1.1", headers=HEADERS, json=entry(answer="The story.")
        )
        scored = await client.put(
            "/v1/jobs/library/1.1/human-percent", headers=HEADERS,
            json={"percent": 92},
        )
        assert scored.status_code == 200 and scored.json()["human_percent"] == 92
        assert connection.changes[-1][3] == "update"
        assert connection.changes[-1][4].obj == {"human_percent": None}
        assert connection.changes[-1][5].obj == {"human_percent": 92}

        # A no-change score records nothing.
        changes = len(connection.changes)
        assert (await client.put(
            "/v1/jobs/library/1.1/human-percent", headers=HEADERS,
            json={"percent": 92},
        )).status_code == 200
        assert len(connection.changes) == changes

        kept = await client.put(
            "/v1/jobs/library/1.1", headers=HEADERS,
            json=entry(answer="The story.", guidance="Say less."),
        )
        assert kept.status_code == 200 and kept.json()["human_percent"] == 92

        cleared = await client.put(
            "/v1/jobs/library/1.1", headers=HEADERS, json=entry(answer="Edited.")
        )
        assert cleared.status_code == 200 and cleared.json()["human_percent"] is None

        assert (await client.put(
            "/v1/jobs/library/9.9/human-percent", headers=HEADERS,
            json={"percent": 50},
        )).status_code == 404
        for bad in (101, -1):
            assert (await client.put(
                "/v1/jobs/library/1.1/human-percent", headers=HEADERS,
                json={"percent": bad},
            )).status_code == 422


async def test_import_only_while_the_library_is_empty(client: httpx.AsyncClient) -> None:
    async with client:
        imported = await client.post(
            "/v1/jobs/library/import",
            headers=HEADERS,
            json=[entry(), entry("2.2.11", section="note")],
        )
        assert imported.status_code == 201
        assert {row["code"] for row in imported.json()} == {"1.1", "2.2.11"}
        again = await client.post(
            "/v1/jobs/library/import", headers=HEADERS, json=[entry("3.1")]
        )
        assert again.status_code == 409
        assert "already has entries" in again.json()["detail"]


async def test_a_path_and_body_code_mismatch_is_refused(client: httpx.AsyncClient) -> None:
    async with client:
        refused = await client.put(
            "/v1/jobs/library/9.9", headers=HEADERS, json=entry()
        )
        assert refused.status_code == 422


async def test_another_users_resume_entry_is_refused(
    connection: LibraryConnection,
) -> None:
    connection.resume_ids = set()
    store = PostgresLibraryStore(LibraryPool(connection), 5)  # type: ignore[arg-type]
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

    async def override() -> PostgresLibraryStore:
        return store

    app.dependency_overrides[get_library_store] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        refused = await client.put(
            "/v1/jobs/library/4.1",
            headers=HEADERS,
            json=entry("4.1", entry_id=str(ENTRY_ID)),
        )
        assert refused.status_code == 422
        assert "resume entries" in refused.json()["detail"]


@pytest.mark.parametrize(
    "code",
    ["1.1", "2.2.11", "5.1.E2", "6.1.P3", "10"],
)
def test_code_shapes_accept(code: str) -> None:
    LibraryEntryWrite.model_validate(entry(code))


@pytest.mark.parametrize(
    "code",
    ["a.1", "1.2.3.4.5.B", "1..2", "E2", "1.E", "1.a2", "x" * 41, "1.-1"],
)
def test_code_shapes_refuse(code: str) -> None:
    with pytest.raises(ValidationError):
        LibraryEntryWrite.model_validate(entry(code))


def test_blank_fields_normalize() -> None:
    item = LibraryEntryWrite.model_validate(entry(subject="  ", answer=" \n "))
    assert item.subject is None and item.answer is None
