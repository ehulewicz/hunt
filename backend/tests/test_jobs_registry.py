from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from personal_os_backend.jobs_models import AutofillStepCreate
from personal_os_backend.jobs_registry import (
    FillEventCreate,
    normalize_question_key,
    record_fill_events,
)

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")


def event(**overrides: Any) -> dict[str, Any]:
    return {
        "question": "Will you require sponsorship?",
        "kind": "choice_group",
        "attributes": {"tag": "input", "type": "radio", "group": "many"},
        "strategy": "check_control",
        "resolver_id": "builtin:sponsorship",
        "source": "pattern",
        "value_hash": "ab" * 32,
        "stopped_at": None,
        "operated_ok": True,
    } | overrides


def test_a_step_carries_fill_events() -> None:
    step = AutofillStepCreate.model_validate(
        {"step_number": 0, "url": "https://example.com", "fill_events": [event()]}
    )
    assert step.fill_events[0].strategy == "check_control"
    assert AutofillStepCreate.model_validate(
        {"step_number": 0, "url": "https://example.com"}
    ).fill_events == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "checkbox"},
        {"stopped_at": "readback"},
        {"strategy": "Check Control"},
        {"stopped_at": "strategy", "operated_ok": True},
        {"question": ""},
        {"unexpected": 1},
    ],
)
def test_malformed_events_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FillEventCreate.model_validate(event(**overrides))


def test_question_keys_normalize_like_the_answer_library() -> None:
    assert normalize_question_key("  Will you require   SPONSORSHIP? ") == (
        "will you require sponsorship"
    )
    assert normalize_question_key("C++ / C# experience") == "c++ c# experience"


class FakeCursor:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row


class FakeConnection:
    """Answers the control and strategy lookups and records every statement."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.ids: dict[str, UUID] = {}

    async def execute(self, query: str, params: tuple[Any, ...]) -> FakeCursor:
        self.statements.append((query, params))
        if "into app.jobs_questions" in query:
            key = f"question:{params[1]}"
            return FakeCursor({"id": self.ids.setdefault(key, uuid4())})
        if "into app.jobs_patterns" in query:
            return FakeCursor(None)
        if "app.jobs_controls" in query:
            key = f"control:{params[1]}:{params[2].obj}"
            return FakeCursor({"id": self.ids.setdefault(key, uuid4())})
        if "app.jobs_strategies" in query:
            key = f"strategy:{params[1]}:{params[2]}"
            return FakeCursor({"id": self.ids.setdefault(key, uuid4())})
        return FakeCursor(None)


async def test_events_reuse_one_control_and_strategy_lookup_per_run() -> None:
    connection = FakeConnection()
    events = [
        FillEventCreate.model_validate(event()),
        FillEventCreate.model_validate(event(question="Are you a veteran?")),
        FillEventCreate.model_validate(
            event(strategy=None, stopped_at="pattern", operated_ok=None, question="Shift")
        ),
        FillEventCreate.model_validate(event(question="?!")),
    ]
    written = await record_fill_events(
        connection,  # type: ignore[arg-type]
        user_id=USER_ID,
        run_id=uuid4(),
        step=2,
        ats="lever",
        events=events,
    )
    assert written == 3
    lookups = [
        query for query, _ in connection.statements
        if "insert into app.jobs_fill" not in query and "jobs_questions" not in query
    ]
    assert sum("app.jobs_controls" in query for query in lookups) == 1
    assert sum("app.jobs_strategies" in query for query in lookups) == 1
    inserts = [
        params for query, params in connection.statements if "insert into app.jobs_fill" in query
    ]
    assert [params[3] for params in inserts] == [
        "will you require sponsorship", "are you a veteran", "shift",
    ]
    assert inserts[2][9] is None and inserts[2][12] == "pattern"
    assert {params[10] for params in inserts} == {"lever"}


async def test_every_event_records_its_question_once_per_run() -> None:
    connection = FakeConnection()
    events = [
        FillEventCreate.model_validate(event()),
        FillEventCreate.model_validate(event(question="Will you require  SPONSORSHIP?")),
        FillEventCreate.model_validate(event(question="Are you a veteran?")),
    ]
    await record_fill_events(
        connection,  # type: ignore[arg-type]
        user_id=USER_ID, run_id=uuid4(), step=0, ats="ashby", events=events,
    )
    upserts = [params for query, params in connection.statements if "jobs_questions" in query]
    assert [params[1] for params in upserts] == [
        "will you require sponsorship", "are you a veteran",
    ]
    inserts = [
        params for query, params in connection.statements if "insert into app.jobs_fill" in query
    ]
    assert inserts[0][14] == inserts[1][14] != inserts[2][14]  # question_id


@pytest.mark.parametrize(
    ("overrides", "proposed"),
    [
        ({"source": "model", "fact_key": "work_auth.requires_sponsorship"}, True),
        ({"source": "pattern", "fact_key": "work_auth.requires_sponsorship"}, False),
        # The archive is never a suggestion for an application (design §0).
        ({"source": "archive", "fact_key": "work_auth.requires_sponsorship"}, False),
        ({"source": "model", "fact_key": None}, False),
        ({"source": "model", "fact_key": "work_auth.requires_sponsorship",
          "stopped_at": "strategy", "operated_ok": False}, False),
        ({"source": "model", "fact_key": "work_auth.requires_sponsorship",
          "operated_ok": None}, False),
    ],
)
async def test_only_a_read_back_model_fill_proposes_a_candidate(
    overrides: dict[str, Any], proposed: bool
) -> None:
    connection = FakeConnection()
    await record_fill_events(
        connection,  # type: ignore[arg-type]
        user_id=USER_ID, run_id=uuid4(), step=0, ats="ashby",
        events=[FillEventCreate.model_validate(event(**overrides))],
    )
    proposals = [
        (query, params) for query, params in connection.statements
        if "into app.jobs_patterns" in query
    ]
    assert bool(proposals) is proposed
    if proposed:
        query, params = proposals[0]
        assert "'candidate'" in query and "active" not in query
        assert "work_auth.requires_sponsorship" in params
