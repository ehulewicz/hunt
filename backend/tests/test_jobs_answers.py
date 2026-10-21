import re
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
    PatternRefused,
    PatternUpdate,
    PostgresAnswerStore,
    ProseUpdate,
    Question,
    QuestionUpdate,
    _model_refusal,
    answered_by_expression,
    asks_a_question,
    get_answer_store,
    normalize_answer_key,
    runaway_expression,
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


@pytest.mark.parametrize(
    ("label", "kind", "asks"),
    [
        ("Will you require sponsorship?", None, True),
        ("Preferred Last Name", "text_entry", True),
        ("Do you have any questions for us?", None, True),
        ("Yes", None, False),
        ("  No ", "choice_group", False),
        ("Search", None, False),
        ("Zoom level", None, False),
        ("Page number", None, False),
        ("Job specific questions", None, False),
        ("Resume/CV", "file", False),
        ("input|file|||", None, False),
        ("unlabeled", None, False),
        ("?!", None, False),
    ],
)
def test_only_a_label_that_asks_something_is_a_question(
    label: str, kind: str | None, asks: bool
) -> None:
    assert asks_a_question(label, kind) is asks


def test_expressions_decide_a_label_only_when_they_name_one_fact() -> None:
    expressions = [
        (re.compile(r"\bsponsor(ship)?\b", re.IGNORECASE), "work_auth.requires_sponsorship"),
        (re.compile(r"\bvisa\b", re.IGNORECASE), "work_auth.requires_sponsorship"),
        (re.compile(r"\bgit\s*hub\b", re.IGNORECASE), "contact.github_url"),
        (re.compile(r"\blinked\s*in\b", re.IGNORECASE), "contact.linkedin_url"),
    ]
    assert answered_by_expression("Will you need visa sponsorship?", expressions)
    assert answered_by_expression("  GitHub URL ", expressions)
    assert not answered_by_expression("Employed by LinkedIn or GitHub?", expressions)
    assert not answered_by_expression("Preferred Last Name", expressions)


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
    # The worker learning from a verdict names the question by its wording.
    learned = PatternCreate(
        kind="binding", question="Do you require sponsorship?",
        fact_key="work_auth.requires_sponsorship", origin="model", status="active",
    )
    assert learned.question_id is None and learned.question == "Do you require sponsorship?"
    for invalid in (
        {"kind": "binding", "fact_key": "work_auth.us_citizen"},
        {"kind": "binding", "question_id": QUESTION_ID, "question": "Twice?", "fact_key": "a.b"},
        {"kind": "expression", "expression": "x", "question": "Which?", "fact_key": "a.b"},
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


class InboxConnection:
    """Serves the Inbox's reads: expressions, unbound questions, and nothing else."""

    def __init__(self, expressions: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
        self.expressions = expressions
        self.rows = rows
        self._result: list[dict[str, Any]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["InboxConnection"]:
        yield self

    async def execute(self, query: str, params: Any = None) -> "InboxConnection":
        if "kind = 'expression'" in query:
            self._result = self.expressions
        elif "from app.jobs_questions" in query:
            self._result = self.rows
        else:
            self._result = []
        return self

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._result


class InboxPool:
    def __init__(self, connection: InboxConnection) -> None:
        self.inbox = connection

    @asynccontextmanager
    async def connection(self, **_: Any) -> AsyncIterator[InboxConnection]:
        yield self.inbox


async def test_the_inbox_leaves_out_what_a_run_already_fills_and_what_asks_nothing() -> None:
    def row(label: str, *, uploads: bool = False) -> dict[str, Any]:
        return question().model_dump() | {
            "id": uuid4(), "normalized_key": normalize_answer_key(label),
            "sample_label": label, "uploads": uploads,
        }

    rows = [
        row("Will you require sponsorship?"),
        row("Preferred Last Name"),
        row("Yes"),
        row("Resume"),
        row("Resume", uploads=True),
        row("Employed by LinkedIn or GitHub?"),
    ]
    expressions = [
        {"expression": r"\bsponsor", "fact_key": "work_auth.requires_sponsorship"},
        {"expression": r"\bgit\s*hub\b", "fact_key": "contact.github_url"},
        {"expression": r"\blinked\s*in\b", "fact_key": "contact.linkedin_url"},
        {"expression": "unbalanced (", "fact_key": "a.b"},
    ]
    store = PostgresAnswerStore(InboxPool(InboxConnection(expressions, rows)), 5)  # type: ignore[arg-type]

    inbox = await store.inbox(USER_ID)

    assert [item.question.sample_label for item in inbox.questions] == [
        "Preferred Last Name", "Resume", "Employed by LinkedIn or GitHub?",
    ]


class RefusalConnection:
    """Answers each lookup `_model_refusal` makes with the row given for it."""

    def __init__(self, *, disposition: str = "unbound", binding: str | None = None,
                 expression: bool = False) -> None:
        self.answers = {"jobs_questions": {"disposition": disposition},
                        "kind = 'binding'": {"status": binding} if binding else None,
                        "kind = 'expression'": {"?column?": 1} if expression else None}
        self._row: dict[str, Any] | None = None

    async def execute(self, query: str, params: Any = None) -> "RefusalConnection":
        self._row = next(
            (row for marker, row in self.answers.items() if marker in query), None
        )
        return self

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row


@pytest.mark.parametrize(
    ("connection", "refusal"),
    [
        (RefusalConnection(), None),
        (RefusalConnection(disposition="one_off"), "you marked this question one-off"),
        (RefusalConnection(disposition="long_form"), "you marked this question long-form"),
        (RefusalConnection(binding="active"),
         "the question is bound already, and a model never replaces a binding"),
        (RefusalConnection(binding="rejected"), "you rejected or retired this binding"),
    ],
)
async def test_a_binding_a_model_taught_never_undoes_your_decision(
    connection: RefusalConnection, refusal: str | None
) -> None:
    item = PatternCreate(kind="binding", question_id=QUESTION_ID, fact_key="a.b",
                         origin="model", status="active")
    assert await _model_refusal(connection, USER_ID, item) == refusal  # type: ignore[arg-type]


async def test_an_expression_a_model_taught_is_refused_once_you_rejected_it() -> None:
    item = PatternCreate(kind="expression", expression=r"\bphone\b", fact_key="contact.phone",
                         origin="model", status="active")
    assert await _model_refusal(RefusalConnection(), USER_ID, item) is None  # type: ignore[arg-type]
    assert await _model_refusal(
        RefusalConnection(expression=True), USER_ID, item  # type: ignore[arg-type]
    ) == "you rejected or retired this expression"


def test_a_models_expression_may_not_nest_one_repetition_in_another() -> None:
    assert runaway_expression(r"^(\w+\s?)+$") and runaway_expression("(a*)*")
    assert not runaway_expression(r"^\s*(alternate|other)\s+phone(\s+number)?\s*$")
    with pytest.raises(ValidationError):
        PatternCreate(kind="expression", expression=r"^(\w+\s?)+$", fact_key="a.b",
                      origin="model")
    # Your own expressions are yours to write.
    PatternCreate(kind="expression", expression=r"^(\w+\s?)+$", fact_key="a.b")


async def test_a_refused_pattern_is_a_conflict(
    client: httpx.AsyncClient, store: FakeAnswerStore
) -> None:
    async def refuse(user_id: UUID, item: PatternCreate) -> Pattern | None:
        raise PatternRefused("you rejected or retired this binding")

    store.create_pattern = refuse  # type: ignore[method-assign]
    async with client as http:
        response = await http.post(
            "/v1/jobs/patterns",
            json={"kind": "binding", "question": "Which campus?", "fact_key": "a.b",
                  "origin": "model", "status": "active"},
            headers=HEADERS,
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "you rejected or retired this binding"
