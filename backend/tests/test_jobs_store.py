from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

USER_ID = uuid4()
APPLICATION_ID = uuid4()
TASK_ID = uuid4()


class FakeCursor:
    def __init__(self, row: dict[str, Any] | None, rowcount: int) -> None:
        self._row = row
        self.rowcount = rowcount

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row


class FakeTransaction:
    async def __aenter__(self) -> FakeTransaction:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeConnection:
    """Records every parameter the store sends to PostgreSQL."""

    def __init__(
        self,
        task_type: str = "prepare_application",
        task_payload: dict[str, Any] | None = None,
    ) -> None:
        self.parameters: list[Any] = []
        self.queries: list[str] = []
        self.task_type = task_type
        self.task_payload = task_payload or {"application_id": str(APPLICATION_ID)}

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def execute(
        self, query: str, parameters: tuple[Any, ...] | None = None
    ) -> FakeCursor:
        if parameters is not None:
            self.parameters.extend(parameters)
        normalized = " ".join(query.split())
        self.queries.append(normalized)
        if "select task_type, payload" in normalized:
            return FakeCursor(
                {
                    "task_type": self.task_type,
                    "payload": self.task_payload,
                },
                1,
            )
        if "select company.name" in normalized:
            return FakeCursor({"name": "Wells Fargo"}, 1)
        return FakeCursor(None, 1)


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    def connection(self, timeout: float | None = None) -> Any:
        connection = self._connection

        class Context:
            async def __aenter__(self) -> FakeConnection:
                return connection

            async def __aexit__(self, *_: object) -> None:
                return None

        return Context()


def test_no_query_locks_a_grouped_select() -> None:
    """PostgreSQL rejects FOR UPDATE combined with GROUP BY."""
    source = "".join(
        (Path(__file__).resolve().parents[1] / "src/personal_os_backend" / name).read_text()
        for name in (
            "jobs_listings.py", "jobs_applications.py", "jobs_runs.py",
            "jobs_answer_history.py", "jobs_failures.py",
        )
    )
    offending = [
        " ".join(statement.split())
        for statement in source.split('"""')
        if "group by" in statement.lower() and "for update" in statement.lower()
    ]

    assert offending == [], f"these queries would raise at runtime: {offending}"


def test_autofill_run_cte_returns_the_selected_ats() -> None:
    """PostgreSQL exposes only columns named by a data-modifying CTE RETURNING."""
    source = (
        Path(__file__).resolve().parents[1] / "src/personal_os_backend/jobs_runs.py"
    ).read_text()
    query = source.split("async def create_autofill_run", 2)[2].split(
        "async def persist_autofill_step", 1
    )[0]

    assert "returning id, application_id, ats" in " ".join(query.split())


def test_board_numbers_listings_once_before_any_filter() -> None:
    """The row number is a window over the whole board, so every view agrees."""
    from personal_os_backend.jobs_store import BOARD_QUERY

    board = " ".join(BOARD_QUERY.as_string(None).split())
    assert "row_number() over ( order by case when posting.role_families && %s::text[]" in board
    assert "data_science" not in board, "no role family leaves the board"
    assert board.endswith(
        "and application.id is null"
    ), "applied-to listings must leave the board"
    assert "posting.created_at desc, posting.id )" in board, "same-day rows need a tiebreak"
    assert 'as "row"' in board


def test_lists_that_are_addressed_by_row_have_deterministic_order() -> None:
    source = "".join(
        (Path(__file__).resolve().parents[1] / "src/personal_os_backend" / name).read_text()
        for name in ("jobs_applications.py", "jobs_failures.py")
    )
    assert "application.updated_at desc, application.id" in source
    assert "order by last_seen_at desc, id" in source
    answers = (
        Path(__file__).resolve().parents[1] / "src/personal_os_backend/jobs_prose.py"
    ).read_text()
    assert "order by updated_at desc, id" in answers  # the archive, listed by row


def test_a_fix_that_fails_again_goes_back_to_the_queue() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src/personal_os_backend/jobs_failures.py"
    ).read_text()
    upsert = " ".join(source.split("on conflict (user_id, signature)", 1)[1].split()[:120])
    assert "'fixed', 'archived', 'repaired', 'validated' ) then 'open'" in upsert
    # An unsolved issue stays unsolved until --retry or a new fixer API version.
    assert "'unsolved'" not in upsert.split("then 'open'")[0].rsplit("in (", 1)[-1]
    assert "stopped_at = coalesce( excluded.stopped_at" in upsert


def test_repair_statuses_and_candidates_are_part_of_the_failure_contract() -> None:
    from personal_os_backend.jobs_models import AutofillFailureUpdate

    update = AutofillFailureUpdate.model_validate(
        {"status": "repaired", "candidate": {"kind": "strategy", "strategy": "set_value"}}
    )
    assert update.candidate == {"kind": "strategy", "strategy": "set_value"}
    assert AutofillFailureUpdate.model_validate({"status": "validated"}).status == "validated"
    with pytest.raises(ValueError):
        AutofillFailureUpdate.model_validate({"status": "repairing"})


def test_an_unsolved_issue_may_be_archived() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src/personal_os_backend/jobs_failures.py"
    ).read_text()
    transitions = source.split('"unsolved": {', 1)[1].split("}", 1)[0]
    assert '"archived"' in transitions
