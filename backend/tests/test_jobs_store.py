from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from personal_os.jobs_store import PostgresJobsStore

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

    def __init__(self) -> None:
        self.parameters: list[Any] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def execute(
        self, query: str, parameters: tuple[Any, ...] | None = None
    ) -> FakeCursor:
        if parameters is not None:
            self.parameters.extend(parameters)
        normalized = " ".join(query.split())
        if "select task_type, payload" in normalized:
            return FakeCursor(
                {
                    "task_type": "prepare_application",
                    "payload": {"application_id": str(APPLICATION_ID)},
                },
                1,
            )
        if "document_type = 'master_resume'" in normalized:
            return FakeCursor({"id": uuid4()}, 1)
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


@pytest.mark.anyio
async def test_complete_task_sends_adaptable_json_parameters() -> None:
    """psycopg 3 has no dumper for dict, so jsonb values must be wrapped."""
    connection = FakeConnection()
    store = PostgresJobsStore(FakePool(connection), 5.0)

    result = {
        "application_id": str(APPLICATION_ID),
        "posting_description": "Quantitative analytics internship",
        "form_fields": [{"label": "Full name", "required": True}],
        "tailored_resume": {"summary": "Analyst"},
        "storage_path": "jobs-documents/tailored.pdf",
        "resume_patch": {"patches": []},
        "changed_word_ratio": 0.04,
        "changed_paths": ["summary"],
    }

    await store.complete_task(USER_ID, TASK_ID, "worker-1", result)

    bare_dicts = [value for value in connection.parameters if type(value) is dict]
    assert bare_dicts == [], (
        "these dict parameters cannot be adapted by psycopg and raise at runtime: "
        f"{bare_dicts}"
    )
    assert any(isinstance(value, Jsonb) for value in connection.parameters)
