from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from personal_os.jobs_models import (
    AutofillObservation,
    AutofillRunComplete,
    AutofillStepCreate,
)
from personal_os.jobs_store import PostgresJobsStore


class RecordingConnection:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.queries: list[tuple[str, tuple[Any, ...] | None]] = []

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self

    async def execute(self, query: str, params: tuple[Any, ...] | None = None):  # type: ignore[no-untyped-def]
        self.queries.append((query, params))
        return self

    async def fetchone(self) -> dict[str, Any] | None:
        return self.row


class RecordingPool:
    def __init__(self, connection: RecordingConnection) -> None:
        self.recording = connection

    @asynccontextmanager
    async def connection(self, **_: Any):  # type: ignore[no-untyped-def]
        yield self.recording


def running_run(application_id: UUID) -> dict[str, Any]:
    return {"application_id": application_id, "ats": "greenhouse", "adapter_version": "test"}


def question_inserts(connection: RecordingConnection) -> list[tuple[str, tuple[Any, ...] | None]]:
    return [
        (query, params)
        for query, params in connection.queries
        if "insert into app.jobs_application_questions" in query
    ]


def observation(**overrides: Any) -> AutofillObservation:
    fields: dict[str, Any] = {
        "event_index": 0,
        "question": "Why this company?",
        "answer_text": "Because of the mission",
        "source": "model",
        "step_number": 2,
        "page_url": "https://example.test/apply?step=2",
        "control_kind": "textarea",
        "field_fingerprint": "textarea:why-company",
        "resolver_id": "claude_draft",
        "proposed_value": "Because of the mission",
    }
    fields.update(overrides)
    return AutofillObservation(**fields)


def test_step_create_defaults_to_no_observations() -> None:
    item = AutofillStepCreate(step_number=1, url="https://example.test/apply")

    assert item.observations == []


def test_observation_rejects_negative_event_index() -> None:
    with pytest.raises(ValidationError):
        observation(event_index=-1)


def test_observation_rejects_blank_question() -> None:
    with pytest.raises(ValidationError):
        observation(question="   ")


@pytest.mark.asyncio
async def test_step_observations_become_observed_drafted_history_rows() -> None:
    application_id = uuid4()
    run_id = uuid4()
    user_id = uuid4()
    connection = RecordingConnection(running_run(application_id))
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillStepCreate(
        step_number=2,
        url="https://example.test/apply?step=2",
        filled_count=1,
        observations=[observation()],
    )

    assert await store.persist_autofill_step(user_id, run_id, item) == []

    inserts = question_inserts(connection)
    assert len(inserts) == 1
    query, params = inserts[0]
    assert params is not None
    assert "'observed'" in query
    assert "'drafted'" in query
    assert "on conflict (run_id, event_index) do nothing" in query
    assert user_id in params
    assert application_id in params
    assert run_id in params
    assert "Why this company?" in params
    assert "Because of the mission" in params
    assert "model" in params
    assert 2 in params
    assert "https://example.test/apply?step=2" in params
    assert "textarea" in params
    assert "textarea:why-company" in params
    assert "claude_draft" in params
    assert 0 in params


@pytest.mark.asyncio
async def test_step_observations_never_touch_the_answer_library() -> None:
    connection = RecordingConnection(running_run(uuid4()))
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillStepCreate(
        step_number=0,
        url="https://example.test/apply",
        observations=[observation(event_index=index) for index in range(3)],
    )

    assert await store.persist_autofill_step(uuid4(), uuid4(), item) == []

    statements = "\n".join(query for query, _ in connection.queries)
    assert "jobs_answer_library" not in statements
    assert "'approved'" not in statements
    assert len(question_inserts(connection)) == 3


@pytest.mark.asyncio
async def test_step_observation_with_unknown_source_is_stored_as_manual() -> None:
    connection = RecordingConnection(running_run(uuid4()))
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillStepCreate(
        step_number=0,
        url="https://example.test/apply",
        observations=[observation(source="telepathy")],
    )

    await store.persist_autofill_step(uuid4(), uuid4(), item)

    ((_, params),) = question_inserts(connection)
    assert params is not None
    assert "manual" in params
    assert "telepathy" not in params


@pytest.mark.asyncio
async def test_step_without_observations_writes_no_history() -> None:
    connection = RecordingConnection(running_run(uuid4()))
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillStepCreate(step_number=0, url="https://example.test/apply")

    assert await store.persist_autofill_step(uuid4(), uuid4(), item) == []

    assert question_inserts(connection) == []


@pytest.mark.asyncio
async def test_step_for_missing_run_writes_nothing() -> None:
    connection = RecordingConnection(None)
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillStepCreate(
        step_number=0,
        url="https://example.test/apply",
        observations=[observation()],
    )

    assert await store.persist_autofill_step(uuid4(), uuid4(), item) is None

    assert question_inserts(connection) == []


@pytest.mark.asyncio
async def test_completed_model_answer_is_history_without_library_approval() -> None:
    connection = RecordingConnection({"application_id": uuid4()})
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillRunComplete(
        final_url="https://example.test/thanks",
        answers=[
            {"question": "Why this company?", "answer_text": "A tailored draft", "source": "model"}
        ],
    )

    assert await store.complete_autofill_run(uuid4(), uuid4(), item)

    statements = "\n".join(query for query, _ in connection.queries)
    assert "insert into app.jobs_answer_library" not in statements
    assert "insert into app.jobs_application_questions" in statements
    assert "'approved'" not in statements


@pytest.mark.asyncio
async def test_completed_answers_are_final_events_of_their_run() -> None:
    run_id = uuid4()
    connection = RecordingConnection({"application_id": uuid4()})
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillRunComplete(
        final_url="https://example.test/thanks",
        submitted=True,
        answers=[
            {
                "question": "Why this company?",
                "answer_text": "A tailored draft",
                "source": "model",
                "step_number": 2,
                "page_url": "https://example.test/apply?step=2",
                "control_kind": "textarea",
                "field_fingerprint": "textarea:why-company",
                "resolver_id": "claude_draft",
                "proposed_value": "First draft",
            }
        ],
    )

    assert await store.complete_autofill_run(uuid4(), run_id, item)

    ((query, params),) = question_inserts(connection)
    assert params is not None
    assert "'final'" in query
    assert run_id in params
    assert "submitted" in params
    assert "drafted" not in params
    assert "textarea:why-company" in params
    assert "First draft" in params


@pytest.mark.asyncio
async def test_completed_unsubmitted_answers_stay_drafted() -> None:
    connection = RecordingConnection({"application_id": uuid4()})
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillRunComplete(
        final_url="https://example.test/review",
        submitted=False,
        answers=[
            {"question": "Why this company?", "answer_text": "A tailored draft", "source": "model"}
        ],
    )

    assert await store.complete_autofill_run(uuid4(), uuid4(), item)

    ((_, params),) = question_inserts(connection)
    assert params is not None
    assert "drafted" in params
    assert "submitted" not in params
