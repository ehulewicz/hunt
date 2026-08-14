from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from personal_os.jobs_models import MasterResumeContent
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


@pytest.mark.anyio
async def test_preview_completion_stores_the_pdf_on_the_draft() -> None:
    """A preview task carries a document_id, never an application_id."""
    document_id = uuid4()
    connection = FakeConnection(
        task_type="preview_resume", task_payload={"document_id": str(document_id)}
    )
    store = PostgresJobsStore(FakePool(connection), 5.0)

    completed = await store.complete_task(
        USER_ID,
        TASK_ID,
        "worker-1",
        {"document_id": str(document_id), "storage_path": "resumes/preview.pdf"},
    )

    assert completed is True
    assert any("update app.jobs_documents" in query for query in connection.queries)
    assert "resumes/preview.pdf" in connection.parameters
    assert [value for value in connection.parameters if type(value) is dict] == []


@pytest.mark.anyio
async def test_preview_completion_requires_a_storage_path() -> None:
    connection = FakeConnection(
        task_type="preview_resume", task_payload={"document_id": str(uuid4())}
    )
    store = PostgresJobsStore(FakePool(connection), 5.0)

    completed = await store.complete_task(USER_ID, TASK_ID, "worker-1", {})

    assert completed is False


def test_review_query_unwraps_claude_patch_objects() -> None:
    """The worker stores Claude's object; the review model needs the patch list.

    Verified against PostgreSQL 16: the extraction yields a jsonb array for both
    the stored object shape and a bare array.
    """
    source = (Path(__file__).resolve().parents[1] / "src/personal_os/jobs_store.py").read_text()
    review_query = source.split("async def get_document_review", 1)[1]

    assert "jsonb_typeof(run.diff_json->'patch') = 'array'" in review_query
    assert "run.diff_json->'patch'->'patches'" in review_query


def test_no_query_locks_a_grouped_select() -> None:
    """PostgreSQL rejects FOR UPDATE combined with GROUP BY."""
    source = (Path(__file__).resolve().parents[1] / "src/personal_os/jobs_store.py").read_text()
    offending = [
        " ".join(statement.split())
        for statement in source.split('"""')
        if "group by" in statement.lower() and "for update" in statement.lower()
    ]

    assert offending == [], f"these queries would raise at runtime: {offending}"


def test_master_resume_content_rejects_blank_bullets() -> None:
    with pytest.raises(ValueError, match="blank"):
        MasterResumeContent.model_validate(
            {
                "contact": {"name": "Candidate"},
                "education": {"school": "School"},
                "experience": [{"title": "Engineer", "bullets": ["   "]}],
            }
        )


def test_master_resume_content_requires_a_contact_name() -> None:
    with pytest.raises(ValueError):
        MasterResumeContent.model_validate(
            {"contact": {}, "education": {"school": "School"}}
        )


def test_master_resume_content_accepts_the_canonical_shape() -> None:
    content = MasterResumeContent.model_validate(
        {
            "meta": {"slug": "main"},
            "contact": {"name": "Candidate", "email": "candidate@example.com"},
            "education": {"school": "School", "degree": "B.S."},
            "experience": [{"title": "Engineer", "org": "Company", "bullets": ["Built it"]}],
            "projects": [],
            "skills": [{"category": "Languages", "items": "Python"}],
        }
    )

    assert content.experience[0].bullets == ["Built it"]
    # Unknown keys such as meta survive so the worker template keeps its extras.
    assert "meta" in content.model_dump()
