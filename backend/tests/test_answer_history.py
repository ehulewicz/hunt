from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from personal_os_backend.jobs_models import (
    AutofillObservation,
    AutofillRunComplete,
    AutofillStepCreate,
)
from personal_os_backend.jobs_router import PostgresJobsStore


class RecordingConnection:
    """Answers a question upsert with an id and every other read with `row`."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.question_id = uuid4()
        self.queries: list[tuple[str, tuple[Any, ...] | None]] = []
        self._pending: dict[str, Any] | None = row

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield self

    async def execute(self, query: str, params: tuple[Any, ...] | None = None):  # type: ignore[no-untyped-def]
        self.queries.append((query, params))
        self._pending = (
            {"id": self.question_id} if "into app.jobs_questions" in query else self.row
        )
        return self

    async def fetchone(self) -> dict[str, Any] | None:
        return self._pending


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
    assert "jobs_archive_answers" not in statements and "jobs_prose" not in statements
    assert "'approved'" not in statements
    assert len(question_inserts(connection)) == 3
    assert all(
        params is not None and connection.question_id in params
        for _, params in question_inserts(connection)
    )


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
    assert "insert into app.jobs_prose" not in statements  # not submitted, not long-form
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


@pytest.mark.parametrize(
    'target,submitted', [('applied', True), ('skipped', False), ('archived', False)]
)
async def test_finish_saves_lifecycle_with_history(target: str, submitted: bool) -> None:
    from personal_os_backend.jobs_models import AutofillRunFinish

    user_id, application_id = uuid4(), uuid4()
    connection = RecordingConnection({'application_id': application_id})
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillRunFinish(
        final_url='https://example.test', submitted=submitted, application_status=target,
        answers=[{'question': 'City', 'answer_text': 'Atlanta'}],
    )
    assert await store.complete_autofill_run(user_id, uuid4(), item)
    lifecycle = [(query, params) for query, params in connection.queries
                 if 'update app.jobs_applications' in query]
    assert len(lifecycle) == 1
    query, params = lifecycle[0]
    assert params is not None
    assert target in params and user_id in params and application_id in params
    assert "status in ('applying', %s)" in query
    assert len(question_inserts(connection)) == 1


def test_finish_rejects_inconsistent_submission_status() -> None:
    from personal_os_backend.jobs_models import AutofillRunFinish

    with pytest.raises(ValidationError):
        AutofillRunFinish(final_url='https://example.test', submitted=False,
                          application_status='applied')


async def test_finish_conflict_exits_transaction_without_inserting_answers() -> None:
    from personal_os_backend.jobs_models import AutofillRunFinish
    from personal_os_backend.jobs_runs import CompletionConflict

    class ConflictConnection(RecordingConnection):
        rolled_back = False

        @asynccontextmanager
        async def transaction(self):  # type: ignore[no-untyped-def]
            try:
                yield self
            except CompletionConflict:
                self.rolled_back = True
                raise

        async def fetchone(self) -> dict[str, Any] | None:
            if 'update app.jobs_applications' in self.queries[-1][0]:
                return None
            return self.row

    connection = ConflictConnection({'application_id': uuid4()})
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillRunFinish(
        final_url='https://example.test', submitted=False, application_status='archived',
        answers=[{'question': 'City', 'answer_text': 'Atlanta'}],
    )
    with pytest.raises(CompletionConflict):
        await store.complete_autofill_run(uuid4(), uuid4(), item)
    assert connection.rolled_back
    assert question_inserts(connection) == []


@pytest.mark.parametrize('metadata', [
    {'step_number': -1}, {'step_number': {'bad': 1}}, {'resolver_id': {'bad': 1}},
    {'field_fingerprint': ['bad']}, {'page_url': ''}, {'proposed_value': 'x' * 10001},
    {'question': '   '}, {'answer_text': 'x' * 10001},
])
def test_completion_rejects_invalid_answer_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AutofillRunComplete(
            final_url='https://example.test',
            answers=[{'question': 'City', 'answer_text': 'Atlanta', **metadata}],
        )


@pytest.mark.asyncio
async def test_a_submitted_long_form_answer_waits_in_the_inbox_as_prose() -> None:
    application_id = uuid4()
    connection = RecordingConnection({"application_id": application_id})
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillRunComplete(
        final_url="https://example.test/thanks",
        submitted=True,
        answers=[
            {"question": "Why us?", "answer_text": "Because of X", "source": "manual",
             "form": "long"},
            {"question": "Why this team?", "answer_text": "Reused", "source": "prose",
             "form": "long"},
            {"question": "Are you 18?", "answer_text": "Yes", "source": "manual",
             "form": "short"},
        ],
    )

    assert await store.complete_autofill_run(uuid4(), uuid4(), item)

    prose = [
        params for query, params in connection.queries if "insert into app.jobs_prose" in query
    ]
    assert len(prose) == 1
    assert prose[0] is not None
    assert "Because of X" in prose[0] and application_id in prose[0] and "manual" in prose[0]


@pytest.mark.asyncio
async def test_every_runs_writing_is_saved_with_where_each_stretch_came_from() -> None:
    application_id = uuid4()
    connection = RecordingConnection({"application_id": application_id})
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    text = "  I build tools. I also like puzzles."
    item = AutofillRunComplete(
        final_url="https://example.test/apply",
        submitted=False,
        answers=[{
            "question": "Why us?", "answer_text": text, "source": "manual", "form": "long",
            "provenance": [{"start": 2, "end": 17, "source": "model"},
                           {"start": 17, "end": 37, "source": "previous"}],
        }],
    )

    assert await store.complete_autofill_run(uuid4(), uuid4(), item)

    [(query, params)] = [
        (query, params) for query, params in connection.queries if "jobs_prose" in query
    ]
    # The latest run's text replaces the unapproved one written for this application.
    assert "update app.jobs_prose" in query and "where not exists" in query
    saved, origin, spans, submitted = params[:4]
    assert (saved, origin, submitted) == ("I build tools. I also like puzzles.", "manual", False)
    # The spans follow the text as saved, without its leading spaces.
    assert spans.obj == [{"start": 0, "end": 15, "source": "model"},
                         {"start": 15, "end": 35, "source": "previous"}]
    assert saved[0:15] == "I build tools. "


@pytest.mark.parametrize("spans", [
    [{"start": 5, "end": 3, "source": "model"}],
    [{"start": 0, "end": 40, "source": "model"}],
    [{"start": 0, "end": 6, "source": "model"}, {"start": 4, "end": 8, "source": "previous"}],
    [{"start": 0, "end": 4, "source": "you"}],
])
def test_spans_must_be_ordered_apart_and_inside_the_answer(spans: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        AutofillRunComplete(
            final_url="https://example.test",
            answers=[{"question": "Why us?", "answer_text": "Because of X", "form": "long",
                      "provenance": spans}],
        )


@pytest.mark.asyncio
async def test_a_long_form_answer_is_kept_as_writing_the_moment_it_is_seen() -> None:
    application_id = uuid4()
    connection = RecordingConnection(running_run(application_id))
    store = PostgresJobsStore(RecordingPool(connection), 5)  # type: ignore[arg-type]
    item = AutofillStepCreate(
        step_number=0,
        url="https://example.test/apply",
        observations=[
            observation(event_index=0, form="long", answer_text="A draft",
                        provenance=[{"start": 0, "end": 7, "source": "model"}]),
            observation(event_index=1, form="long", answer_text="Reused", source="prose"),
            observation(event_index=2, form="long", answer_text="   "),
        ],
    )

    assert await store.persist_autofill_step(uuid4(), uuid4(), item) == []

    [params] = [p for q, p in connection.queries if "jobs_prose" in q]
    assert params is not None
    # Not submitted yet; the run's end updates the same row.
    assert params[:2] == ("A draft", "model") and params[3] is False
    assert application_id in params
