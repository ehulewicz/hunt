"""The Inbox's Learned section: fixes, branches, unsolved, runs, and retiring a fix."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from personal_os_backend.jobs_changes import PostgresChangeStore
from personal_os_backend.jobs_learned import PostgresLearnedStore


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
    async def transaction(self) -> AsyncIterator[ScriptConnection]:
        yield self

    async def execute(self, query: str, params: Any = None) -> ScriptConnection:
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


USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
NOW = datetime.now(UTC)
FILE = "workers/src/personal_os_worker/jobs/registry/kinds/learned/odd_widget.py"


def store_for(connection: ScriptConnection) -> PostgresLearnedStore:
    return PostgresLearnedStore(ScriptPool(connection), 5)  # type: ignore[arg-type]


def changes_store(connection: ScriptConnection) -> PostgresChangeStore:
    return PostgresChangeStore(ScriptPool(connection), 5)  # type: ignore[arg-type]


def code_change(**over: Any) -> dict[str, Any]:
    detail = {"cause": "control_error", "question": "Widget?",
              "files": [FILE], "summary": "captured the widget",
              "probe": {"ok": True}}
    detail.update(over.pop("after", {}))
    return change_row(
        subject="code",
        subject_id="abc1234",
        after=detail,
        origin="worker",
        **over,
    )


async def test_the_learned_report_has_all_four_sections() -> None:
    fix = code_change()
    run = {"id": uuid4(), "application_url": "https://a.com/x", "final_url": None,
           "created_at": NOW, "model_calls": 4,
           "model_usage": {"pattern": {"calls": 2}, "audit": {"calls": 2}}}

    def answer(query: str, _params: Any) -> list[dict[str, Any]]:
        if "subject = 'code'" in query:
            return [fix]
        if "jobs_patterns" in query:
            return [{"id": uuid4(), "fact_key": "contact.name",
                     "expression": "first + ' ' + last", "question_label": None,
                     "created_at": NOW}]
        if "failure_issues" in query:
            signature = "generic|control_error|problem|control_error|a.com|w1"
            return [{"id": uuid4(), "signature": signature,
                     "category": "control_error", "field_label": "Widget?",
                     "verification_notes": "attempts 3 of 3", "created_at": NOW}]
        if "jobs_autofill_runs" in query:
            return [run]
        return []

    connection = ScriptConnection(answer)
    report = await store_for(connection).learned(USER_ID)

    [fix_item] = report.fixes
    assert fix_item.status == "trial" and fix_item.commit == "abc1234"
    assert fix_item.file == FILE and fix_item.summary == "captured the widget"
    [branch] = report.branches
    assert branch.fact_key == "contact.name" and branch.expression
    [unsolved] = report.unsolved
    assert unsolved.site == "a.com" and unsolved.attempts == "attempts 3 of 3"
    [run_item] = report.runs
    assert run_item.model_calls == 4 and run_item.model_usage["pattern"]["calls"] == 2


async def test_a_proven_fix_reports_proven() -> None:
    kept = code_change()
    proven = code_change(after={"outcome": "proven", "file": FILE})

    def answer(query: str, _params: Any) -> list[dict[str, Any]]:
        if "subject = 'code'" in query:
            return [kept, proven]
        return []

    connection = ScriptConnection(answer)
    report = await store_for(connection).learned(USER_ID)
    assert report.fixes[0].status == "proven"


async def test_retire_records_a_retire_change_as_the_user() -> None:
    change = code_change()

    def answer(query: str, _params: Any) -> list[dict[str, Any]]:
        if "action = 'retire'" in query:
            return []
        return [change]

    connection = ScriptConnection(answer)
    assert await changes_store(connection).retire_code_change(USER_ID, change["id"]) is True
    [retire] = connection.changes
    assert retire["action"] == "retire" and retire["origin"] == "you"
    assert retire["subject_id"] == "abc1234"


async def test_retiring_twice_is_refused() -> None:
    change = code_change()

    def answer(query: str, _params: Any) -> list[dict[str, Any]]:
        if "action = 'retire'" in query:
            return [{"id": uuid4()}]
        return [change]

    connection = ScriptConnection(answer)
    assert await changes_store(connection).retire_code_change(USER_ID, change["id"]) is False
    assert connection.changes == []


async def test_an_unknown_change_returns_none() -> None:
    connection = ScriptConnection(lambda _q, _p: [])
    assert await changes_store(connection).retire_code_change(USER_ID, uuid4()) is None
