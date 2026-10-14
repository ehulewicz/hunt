from collections.abc import AsyncIterator
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
    ArchivedAnswer,
    Inbox,
    Pattern,
    PatternCreate,
    PatternUpdate,
    ProseUpdate,
    Question,
    QuestionUpdate,
    get_answer_store,
    normalize_answer_key,
)
from personal_os_backend.main import create_app
from personal_os_backend.models import Principal

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
HEADERS = {"Authorization": "Bearer approved"}
QUESTION_ID = uuid4()
NOW = datetime(2026, 10, 14, tzinfo=UTC)


def test_question_keys_ignore_case_spacing_and_harmless_punctuation() -> None:
    assert normalize_answer_key("  Why this   company?! ") == "why this company"
    assert normalize_answer_key("C++ / C# experience") == "c++ c# experience"
    assert normalize_answer_key("?!") == ""


def test_a_binding_names_a_question_and_an_expression_compiles() -> None:
    binding = PatternCreate(
        kind="binding", question_id=QUESTION_ID, fact_key="work_auth.requires_sponsorship"
    )
    assert binding.status == "candidate" and binding.origin == "inbox"
    expression = PatternCreate(
        kind="expression",
        expression=r"\bsponsor(ship)?\b",
        fact_key="work_auth.requires_sponsorship",
    )
    assert expression.question_id is None
    for invalid in (
        {"kind": "binding", "fact_key": "work_auth.us_citizen"},
        {"kind": "binding", "question_id": QUESTION_ID, "fact_key": "Not A Key"},
        {"kind": "expression", "expression": "unbalanced (paren", "fact_key": "a.b"},
        {"kind": "expression", "expression": "x", "question_id": QUESTION_ID, "fact_key": "a.b"},
        {"kind": "binding", "question_id": QUESTION_ID, "fact_key": "a.b", "formatter": "Bad"},
    ):
        with pytest.raises(ValidationError):
            PatternCreate.model_validate(invalid)


def test_pattern_is_reached_by_accepting_a_binding_not_set_directly() -> None:
    with pytest.raises(ValidationError):
        QuestionUpdate.model_validate({"disposition": "pattern"})
    assert QuestionUpdate(disposition="one_off").disposition == "one_off"
    with pytest.raises(ValidationError):
        ProseUpdate.model_validate({})
    with pytest.raises(ValidationError):
        PatternUpdate.model_validate({"status": "validated"})


def question() -> Question:
    return Question(
        id=QUESTION_ID,
        normalized_key="will you require sponsorship",
        sample_label="Will you require sponsorship?",
        form="short",
        disposition="unbound",
        pattern_id=None,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


def pattern(status: str = "candidate") -> Pattern:
    return Pattern(
        id=uuid4(),
        resolver_id="binding:x",
        kind="binding",
        expression=None,
        question_id=QUESTION_ID,
        question_key="will you require sponsorship",
        question_label="Will you require sponsorship?",
        fact_key="work_auth.requires_sponsorship",
        formatter=None,
        origin="archive",
        archive_ref="archive:sponsorship",
        status=status,  # type: ignore[arg-type]
        created_at=NOW,
        updated_at=NOW,
    )


class FakeAnswerStore:
    def __init__(self) -> None:
        self.patterns: dict[UUID, Pattern] = {}
        self.created: list[PatternCreate] = []

    async def list_questions(self, user_id: UUID, disposition: Any) -> list[Question]:
        return [question()]

    async def update_question(
        self, user_id: UUID, question_id: UUID, item: QuestionUpdate
    ) -> Question | None:
        if question_id != QUESTION_ID:
            return None
        return question().model_copy(update={"disposition": item.disposition})

    async def list_patterns(self, user_id: UUID, statuses: list[str]) -> list[Pattern]:
        return [item for item in self.patterns.values() if item.status in statuses]

    async def create_pattern(self, user_id: UUID, item: PatternCreate) -> Pattern | None:
        if item.question_id not in (None, QUESTION_ID):
            return None
        self.created.append(item)
        created = pattern(item.status).model_copy(update={"origin": item.origin})
        self.patterns[created.id] = created
        return created

    async def update_pattern(
        self, user_id: UUID, pattern_id: UUID, item: PatternUpdate
    ) -> Pattern | None:
        if pattern_id not in self.patterns:
            return None
        self.patterns[pattern_id] = self.patterns[pattern_id].model_copy(
            update={"status": item.status}
        )
        return self.patterns[pattern_id]

    async def list_archive(self, user_id: UUID) -> list[ArchivedAnswer]:
        return [
            ArchivedAnswer(
                id=uuid4(), question="Are you 18?", normalized_key="are you 18",
                answer_text="Yes", reuse_policy="exact", approved=True, updated_at=NOW,
            )
        ]

    async def inbox(self, user_id: UUID) -> Inbox:
        return Inbox(candidates=[], questions=[], prose=[])


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeValidator:
    async def verify(self, token: str) -> Principal:
        return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", None)


@pytest.fixture
def store() -> FakeAnswerStore:
    return FakeAnswerStore()


@pytest.fixture
def client(store: FakeAnswerStore) -> httpx.AsyncClient:
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

    async def override() -> FakeAnswerStore:
        return store

    app.dependency_overrides[get_answer_store] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_a_candidate_is_accepted_and_then_listed_as_active(
    client: httpx.AsyncClient, store: FakeAnswerStore
) -> None:
    async with client:
        created = await client.post(
            "/v1/jobs/patterns",
            headers=HEADERS,
            json={
                "kind": "binding",
                "question_id": str(QUESTION_ID),
                "fact_key": "work_auth.requires_sponsorship",
                "origin": "archive",
            },
        )
        assert created.status_code == 201 and created.json()["status"] == "candidate"
        pattern_id = created.json()["id"]

        listed = await client.get("/v1/jobs/patterns", headers=HEADERS)
        assert [item["status"] for item in listed.json()] == ["candidate"]

        accepted = await client.patch(
            f"/v1/jobs/patterns/{pattern_id}", headers=HEADERS, json={"status": "active"}
        )
        assert accepted.json()["status"] == "active"
        active = await client.get("/v1/jobs/patterns?status=active", headers=HEADERS)
        assert [item["id"] for item in active.json()] == [pattern_id]

        missing = await client.patch(
            f"/v1/jobs/patterns/{uuid4()}", headers=HEADERS, json={"status": "rejected"}
        )
        assert missing.status_code == 404
        foreign = await client.post(
            "/v1/jobs/patterns",
            headers=HEADERS,
            json={"kind": "binding", "question_id": str(uuid4()), "fact_key": "a.b"},
        )
        assert foreign.status_code == 404


async def test_inbox_questions_and_archive_are_readable(client: httpx.AsyncClient) -> None:
    async with client:
        inbox = await client.get("/v1/jobs/inbox", headers=HEADERS)
        assert inbox.json() == {"candidates": [], "questions": [], "prose": []}
        questions = await client.get("/v1/jobs/questions?disposition=unbound", headers=HEADERS)
        assert questions.json()[0]["sample_label"] == "Will you require sponsorship?"
        one_off = await client.patch(
            f"/v1/jobs/questions/{QUESTION_ID}", headers=HEADERS, json={"disposition": "one_off"}
        )
        assert one_off.json()["disposition"] == "one_off"
        archive = await client.get("/v1/jobs/archive/answers", headers=HEADERS)
        assert archive.json()[0]["answer_text"] == "Yes"
        assert (await client.get("/v1/jobs/answers", headers=HEADERS)).status_code == 404
