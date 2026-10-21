from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from personal_os_backend.config import Settings
from personal_os_backend.jobs_answers import (
    PatternCreate,
    PatternUpdate,
    PostgresAnswerStore,
    ProseUpdate,
    QuestionUpdate,
)
from personal_os_backend.jobs_changes import (
    Change,
    CodeChangeCreate,
    Failure,
    PostgresChangeStore,
    Sighting,
    Verification,
    get_change_store,
    record_change,
)
from personal_os_backend.jobs_facts import FactWrite, PostgresFactStore
from personal_os_backend.main import create_app
from personal_os_backend.models import Principal

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
HEADERS = {"Authorization": "Bearer approved"}
NOW = datetime(2026, 10, 21, tzinfo=UTC)
RUN_ID = uuid4()
PENDING_ID = uuid4()


def change_row(**fields: Any) -> dict[str, Any]:
    return {
        "id": uuid4(), "subject": "pattern", "subject_id": "x", "action": "create",
        "before": None, "after": None, "origin": "you", "run_id": None, "question_key": None,
        "failure_issue_id": None, "status": None, "sampled": None, "failed": None,
        "created_at": NOW, "verified_at": None,
    } | fields


class ScriptConnection:
    """A connection whose every query is answered by `answer(query, params)`; it keeps
    each query it ran, and the changes inserted, for the test to read back."""

    def __init__(self, answer: Callable[[str, Any], list[dict[str, Any]]]) -> None:
        self.answer = answer
        self.queries: list[tuple[str, Any]] = []
        self.changes: list[dict[str, Any]] = []
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["ScriptConnection"]:
        yield self

    async def execute(self, query: str, params: Any = None) -> "ScriptConnection":
        self.queries.append((query, params))
        if "insert into app.jobs_changes" in query:
            (_, subject, subject_id, action, before, after, origin, run_id, _, question_key,
             issue_id, _, status, sampled, failed, verified) = params
            row = change_row(
                subject=subject, subject_id=subject_id, action=action,
                before=before.obj if before is not None else None,
                after=after.obj if after is not None else None, origin=origin, run_id=run_id,
                question_key=question_key, failure_issue_id=issue_id, status=status,
                sampled=sampled, failed=failed, verified_at=NOW if verified else None,
            )
            self.changes.append(row)
            self._rows = [row]
        else:
            self._rows = self.answer(query, params)
        self.rowcount = len(self._rows)
        return self

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class ScriptPool:
    def __init__(self, connection: ScriptConnection) -> None:
        self.held = connection

    @asynccontextmanager
    async def connection(self, **_: Any) -> AsyncIterator[ScriptConnection]:
        yield self.held


def nothing(query: str, params: Any) -> list[dict[str, Any]]:
    return []


async def test_a_change_is_verified_exactly_when_a_replay_reported() -> None:
    connection = ScriptConnection(nothing)
    failure = Failure(run_id=RUN_ID, question_key="phone extension")
    failed = await record_change(
        connection, USER_ID, subject="pattern", subject_id="p", action="create",  # type: ignore[arg-type]
        before=None, after={"status": "candidate"}, origin="model", failure=failure,
        verification=Verification(sampled=40, failed=3),
    )
    assert (failed.status, failed.sampled, failed.failed) == ("failed", 40, 3)
    assert failed.verified_at is not None and failed.question_key == "phone extension"
    passed = await record_change(
        connection, USER_ID, subject="pattern", subject_id="p", action="create",  # type: ignore[arg-type]
        before=None, after=None, origin="model", verification=Verification(sampled=40, failed=0),
    )
    assert passed.status == "passed"
    pending = await record_change(
        connection, USER_ID, subject="code", subject_id="abc1234", action="create",  # type: ignore[arg-type]
        before=None, after=None, origin="repair", pending=True,
    )
    assert pending.status == "pending" and pending.verified_at is None
    yours = await record_change(
        connection, USER_ID, subject="fact", subject_id="a.b", action="update",  # type: ignore[arg-type]
        before={"value": 1}, after={"value": 2}, origin="you",
    )
    assert yours.status is None and yours.sampled is None
    # The run is taken only when it is this user's.
    insert = next(query for query, _ in connection.queries if "jobs_changes" in query)
    assert "from app.jobs_autofill_runs where id = %s and user_id = %s" in insert


def test_a_verification_and_its_failure_are_shaped() -> None:
    with pytest.raises(ValidationError):
        Verification(sampled=2, failed=3)
    with pytest.raises(ValidationError):
        Failure(question_key="Not Normalized?")
    assert Failure(question_key="c++ c# experience").question_key == "c++ c# experience"
    with pytest.raises(ValidationError):
        CodeChangeCreate(commit="HEAD")
    assert CodeChangeCreate(commit="5f666c2").origin == "repair"


def test_a_pattern_that_failed_its_replay_never_arrives_active() -> None:
    failed = Verification(sampled=10, failed=1)
    with pytest.raises(ValidationError):
        PatternCreate(kind="expression", expression=r"\bname\b", fact_key="identity.first_name",
                      origin="model", status="active", verification=failed)
    waiting = PatternCreate(kind="expression", expression=r"\bname\b",
                            fact_key="identity.first_name", origin="model",
                            status="candidate", verification=failed)
    assert waiting.verification == failed


def pattern_row(status: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": fields.get("id", uuid4()), "resolver_id": "binding:x", "kind": "binding",
        "expression": None, "question_id": fields.get("question_id", uuid4()),
        "question_key": "phone extension", "question_label": "Phone Extension",
        "fact_key": fields.get("fact_key", "contact.phone_extension"), "formatter": None,
        "origin": "model", "archive_ref": None, "status": status, "created_at": NOW,
        "updated_at": NOW,
    }


async def test_retiring_a_pattern_records_it_before_and_after() -> None:
    pattern_id = uuid4()
    states = iter(["active", "retired"])

    def answer(query: str, params: Any) -> list[dict[str, Any]]:
        if "from app.jobs_patterns pattern" in query and "pattern.id = %s" in query:
            return [pattern_row(next(states), id=pattern_id)]
        return []

    connection = ScriptConnection(answer)
    store = PostgresAnswerStore(ScriptPool(connection), 5)  # type: ignore[arg-type]

    await store.update_pattern(USER_ID, pattern_id, PatternUpdate(status="retired", origin="inbox"))

    [change] = connection.changes
    assert (change["subject"], change["subject_id"], change["action"], change["origin"]) == (
        "pattern", str(pattern_id), "retire", "inbox"
    )
    assert change["before"]["status"] == "active" and change["after"]["status"] == "retired"


@pytest.mark.parametrize("disposition", ["one_off", "long_form", "unbound"])
async def test_changing_question_disposition_records_its_retired_binding(disposition: str) -> None:
    old = pattern_row("active")

    def answer(query: str, params: Any) -> list[dict[str, Any]]:
        if "from app.jobs_patterns pattern" in query:
            return [old]
        return []

    connection = ScriptConnection(answer)
    store = PostgresAnswerStore(ScriptPool(connection), 5)  # type: ignore[arg-type]
    await store.update_question(
        USER_ID, old["question_id"], QuestionUpdate(disposition=disposition),
    )

    [change] = connection.changes
    assert (change["subject_id"], change["action"], change["origin"]) == (
        str(old["id"]), "retire", "inbox",
    )
    assert change["before"]["status"] == "active"
    assert change["after"]["status"] == "retired"


async def test_a_binding_that_replaces_another_records_both() -> None:
    question_id, old_id, new_id = uuid4(), uuid4(), uuid4()

    def answer(query: str, params: Any) -> list[dict[str, Any]]:
        if "select 1 from app.jobs_questions" in query:
            return [{"?column?": 1}]
        if "insert into app.jobs_patterns" in query:
            return [{"id": new_id}]
        if "select kind, question_id" in query:
            return [{"kind": "binding", "question_id": question_id}]
        if "pattern.status = 'active' and pattern.id <> %s" in query:
            return [pattern_row("active", id=old_id, question_id=question_id,
                                fact_key="contact.phone")]
        if "pattern.id = %s" in query:
            return [pattern_row("active", id=new_id, question_id=question_id)]
        return []

    connection = ScriptConnection(answer)
    store = PostgresAnswerStore(ScriptPool(connection), 5)  # type: ignore[arg-type]
    item = PatternCreate(
        kind="binding", question_id=question_id, fact_key="contact.phone_extension",
        origin="model", status="active", failure=Failure(run_id=RUN_ID, question_key="ext"),
        verification=Verification(sampled=12, failed=0),
    )

    await store.create_pattern(USER_ID, item)

    retired, created = connection.changes
    assert (retired["subject_id"], retired["action"], retired["origin"]) == (
        str(old_id), "retire", "model"
    )
    assert retired["after"]["status"] == "retired" and retired["status"] is None
    assert (created["subject_id"], created["action"], created["status"]) == (
        str(new_id), "create", "passed"
    )
    assert created["run_id"] == RUN_ID and created["question_key"] == "ext"


async def test_every_fact_write_is_a_change_keyed_by_the_fact() -> None:
    rows: dict[str, dict[str, Any]] = {}

    def answer(query: str, params: Any) -> list[dict[str, Any]]:
        key = params[1] if params else None
        if query.lstrip().startswith("select") and "from app.jobs_facts" in query:
            return [rows[key]] if key in rows else []
        if "insert into app.jobs_facts" in query:
            rows[key] = {"key": key, "label": params[2], "value": params[3].obj,
                         "value_type": params[4], "verified_at": NOW, "updated_at": NOW}
            return [rows[key]]
        if "update app.jobs_facts" in query:
            return [rows[key]] if key in rows else []
        if "delete from app.jobs_facts" in query:
            return [rows.pop(key)] if key in rows else []
        return []

    connection = ScriptConnection(answer)
    store = PostgresFactStore(ScriptPool(connection), 5)  # type: ignore[arg-type]

    await store.set_fact(USER_ID, "identity.middle_name", FactWrite(
        value="Alexander", label="Middle name", value_type="text", origin="inbox",
        failure=Failure(run_id=RUN_ID, question_key="middle name"),
    ))
    await store.set_fact(USER_ID, "identity.middle_name", FactWrite(value="A."))
    # Saving the same verified value again must not manufacture another change.
    await store.set_fact(USER_ID, "identity.middle_name", FactWrite(value="A."))
    await store.verify_fact(USER_ID, "identity.middle_name")
    assert await store.delete_fact(USER_ID, "identity.middle_name")
    assert not await store.delete_fact(USER_ID, "identity.middle_name")

    actions = [(change["action"], change["origin"]) for change in connection.changes]
    assert actions == [("create", "inbox"), ("update", "you"), ("update", "you"),
                       ("delete", "you")]
    created, updated, _, deleted = connection.changes
    assert {change["subject_id"] for change in connection.changes} == {"identity.middle_name"}
    assert created["before"] is None and created["after"]["value"] == "Alexander"
    assert created["question_key"] == "middle name"
    assert (updated["before"]["value"], updated["after"]["value"]) == ("Alexander", "A.")
    assert deleted["after"] is None and deleted["before"]["value"] == "A."


class FakeChangeStore:
    def __init__(self) -> None:
        self.code: list[CodeChangeCreate] = []
        self.filters: dict[str, Any] = {}

    async def list_changes(self, user_id: UUID, **filters: Any) -> list[Change]:
        self.filters = filters
        return [Change.model_validate(change_row(status="failed", sampled=5, failed=1,
                                                 verified_at=NOW))]

    async def create_code_change(self, user_id: UUID, item: CodeChangeCreate) -> Change:
        self.code.append(item)
        return Change.model_validate(change_row(
            subject="code", subject_id=item.commit, origin=item.origin, status="pending",
        ))

    async def resolve_change(
        self, user_id: UUID, change_id: UUID, verification: Verification
    ) -> Change | None:
        if change_id != PENDING_ID:
            return None
        return Change.model_validate(change_row(
            id=change_id, status="failed" if verification.failed else "passed",
            sampled=verification.sampled, failed=verification.failed, verified_at=NOW,
        ))

    async def sightings(self, user_id: UUID, *, limit: int) -> list[Sighting]:
        return [Sighting(
            question="Phone Extension", question_key="phone extension", kind="text_entry",
            attributes={"tag": "input", "type": "text"}, control="text", options=None,
            answer="12",
            fact_key=None, formatter=None, value_hash=None, seen_at=NOW,
        )][:limit]


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeValidator:
    async def verify(self, token: str) -> Principal:
        return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", None)


@pytest.fixture
def changes() -> FakeChangeStore:
    return FakeChangeStore()


@pytest.fixture
def client(changes: FakeChangeStore) -> httpx.AsyncClient:
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

    async def override() -> FakeChangeStore:
        return changes

    app.dependency_overrides[get_change_store] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_history_code_changes_and_sightings_are_served(
    client: httpx.AsyncClient, changes: FakeChangeStore
) -> None:
    async with client:
        listed = await client.get(
            "/v1/jobs/changes", headers=HEADERS,
            params={"subject": "pattern", "subject_id": "p1", "status": "failed"},
        )
        assert listed.status_code == 200 and listed.json()[0]["failed"] == 1
        assert changes.filters == {"subject": "pattern", "subject_id": "p1",
                                   "status": "failed", "limit": 100}
        made = await client.post("/v1/jobs/changes", headers=HEADERS, json={
            "commit": "5f666c2", "failure": {"question_key": "phone extension"},
            "verification": {"sampled": 50, "failed": 0},
        })
        assert made.status_code == 201 and made.json()["subject_id"] == "5f666c2"
        assert changes.code[0].verification == Verification(sampled=50, failed=0)
        refused = await client.post("/v1/jobs/changes", headers=HEADERS,
                                    json={"commit": "not a commit"})
        assert refused.status_code == 422
        sightings = await client.get("/v1/jobs/sightings", headers=HEADERS)
        assert sightings.json()[0]["answer"] == "12"
        assert (await client.get("/v1/jobs/changes")).status_code == 401



async def test_a_fixers_commit_carries_its_detail_and_a_pending_change_resolves_once(
    client: httpx.AsyncClient, changes: FakeChangeStore
) -> None:
    issue = uuid4()
    async with client:
        made = await client.post("/v1/jobs/changes", headers=HEADERS, json={
            "commit": "abc1234", "origin": "worker", "parent": "5f666c2",
            "failure": {"run_id": str(RUN_ID), "question_key": "years", "issue_id": str(issue)},
            "detail": {"files": ["workers/src/x.py"], "attempts": 2, "probe": "read back"},
        })
        assert made.status_code == 201
        assert changes.code[0].failure is not None and changes.code[0].failure.issue_id == issue
        resolved = await client.patch(
            f"/v1/jobs/changes/{PENDING_ID}/verification", headers=HEADERS,
            json={"sampled": 30, "failed": 0},
        )
        assert resolved.status_code == 200 and resolved.json()["status"] == "passed"
        gone = await client.patch(
            f"/v1/jobs/changes/{uuid4()}/verification", headers=HEADERS,
            json={"sampled": 30, "failed": 0},
        )
        assert gone.status_code == 404


async def test_a_code_change_records_its_parent_detail_and_failure_issue() -> None:
    connection = ScriptConnection(nothing)
    store = PostgresChangeStore(ScriptPool(connection), 5)  # type: ignore[arg-type]
    issue = uuid4()
    change = await store.create_code_change(USER_ID, CodeChangeCreate(
        commit="abc1234", origin="worker", parent="5f666c2",
        failure=Failure(run_id=RUN_ID, issue_id=issue), detail={"attempts": 1},
    ))
    assert change.before == {"commit": "5f666c2"}
    assert change.after == {"commit": "abc1234", "attempts": 1}
    assert change.failure_issue_id == issue and change.status == "pending"
    insert = next(query for query, _ in connection.queries if "jobs_changes" in query)
    # The issue is linked only when it is this user's.
    assert "from app.jobs_autofill_failure_issues where id = %s and user_id = %s" in insert


async def test_prose_and_question_dispositions_are_changes_too() -> None:
    prose_id, question_id = uuid4(), uuid4()
    texts = iter(["First draft.", "First draft, edited."])

    def prose_row() -> dict[str, Any]:
        return {"id": prose_id, "question_id": question_id, "question_label": "Why us?",
                "application_id": None, "company_name": None, "text": next(texts),
                "approved": False, "origin": "model", "provenance": None, "submitted": False,
                "created_at": NOW, "updated_at": NOW}

    def answer(query: str, params: Any) -> list[dict[str, Any]]:
        if "from app.jobs_prose prose" in query:
            return [prose_row()]
        if "update app.jobs_prose" in query:
            return [{"id": prose_id}]
        if "select disposition from app.jobs_questions" in query:
            return [{"disposition": "unbound"}]
        if "update app.jobs_questions" in query:
            return [{"id": question_id, "normalized_key": "why us", "sample_label": "Why us?",
                     "form": "short", "disposition": "long_form", "pattern_id": None,
                     "first_seen_at": NOW, "last_seen_at": NOW}]
        return []

    connection = ScriptConnection(answer)
    store = PostgresAnswerStore(ScriptPool(connection), 5)  # type: ignore[arg-type]
    await store.update_prose(USER_ID, prose_id, ProseUpdate(text="First draft, edited.",
                                                            origin="inbox"))
    await store.update_question(USER_ID, question_id, QuestionUpdate(disposition="long_form"))

    prose, question = connection.changes
    assert (prose["subject"], prose["action"], prose["origin"]) == ("prose", "update", "inbox")
    assert (prose["before"]["text"], prose["after"]["text"]) == (
        "First draft.", "First draft, edited.")
    assert (question["subject"], question["subject_id"]) == ("question", str(question_id))
    assert (question["before"], question["after"]) == (
        {"disposition": "unbound"}, {"disposition": "long_form"})
