from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from personal_os_backend.auth import Principal
from personal_os_backend.config import Settings
from personal_os_backend.jobs_career import (
    PostgresResumeRebuilder,
    ResumeConflict,
    get_resume_rebuilder,
)
from personal_os_backend.jobs_career_pdf import PdfError
from personal_os_backend.jobs_changes import Failure
from personal_os_backend.jobs_facts import (
    Fact,
    FactWrite,
    PostgresFactStore,
    get_fact_store,
    validate_fact_value,
)
from personal_os_backend.jobs_facts_read_only import READ_ONLY_FACTS, read_only_refusal
from personal_os_backend.main import create_app

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
HEADERS = {"Authorization": "Bearer approved"}


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeValidator:
    async def verify(self, token: str) -> Principal:
        return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", None)


class FakeFactStore:
    """Mirrors PostgresFactStore's defaulting and validation over a dict."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def list_facts(self, user_id: UUID) -> list[Fact]:
        return [Fact.model_validate(self.rows[key]) for key in sorted(self.rows)]

    async def set_fact(
        self, user_id: UUID, key: str, item: FactWrite, *, after: Any = None
    ) -> Fact:
        existing = self.rows.get(key, {})
        label = item.label or existing.get("label")
        value_type = item.value_type or existing.get("value_type")
        if not label or not value_type:
            raise ValueError("A new fact needs a label and a value_type")
        backup = dict(self.rows)
        now = datetime.now(UTC)
        self.rows[key] = {
            "key": key,
            "label": label,
            "value": validate_fact_value(value_type, item.value),
            "value_type": value_type,
            "verified_at": now if item.verified else None,
            "updated_at": now,
        }
        if after is not None:
            try:
                await after(self)
            except Exception:
                self.rows = backup  # a failed hook rolls the row back
                raise
        return Fact.model_validate(self.rows[key])

    async def verify_fact(self, user_id: UUID, key: str) -> Fact | None:
        if key not in self.rows:
            return None
        self.rows[key]["verified_at"] = datetime.now(UTC)
        return Fact.model_validate(self.rows[key])

    async def delete_fact(self, user_id: UUID, key: str, *, after: Any = None) -> bool:
        row = self.rows.pop(key, None)
        if row is None:
            return False
        if after is not None:
            try:
                await after(self)
            except Exception:
                self.rows[key] = row  # a failed hook rolls the delete back
                raise
        return True


class FakeRebuilder:
    """prepare() records the header it was asked to build with."""

    def __init__(self) -> None:
        self.headers: list[dict[str, Any]] = []
        self.ran = False
        self.build_error: Exception | None = None
        self.after_error: Exception | None = None
        self.returns_none = False

    async def prepare(self, user_id: UUID, header: dict[str, Any]) -> Any:
        self.headers.append(header)
        if self.build_error is not None:
            raise self.build_error
        if self.returns_none:
            return None

        async def after(connection: Any) -> None:
            self.ran = True
            if self.after_error is not None:
                raise self.after_error

        return after


@pytest.fixture
def store() -> FakeFactStore:
    return FakeFactStore()


@pytest.fixture
def rebuilder() -> FakeRebuilder:
    return FakeRebuilder()


@pytest.fixture
async def client(
    store: FakeFactStore, rebuilder: FakeRebuilder
) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url="postgresql://postgres:password@localhost:5432/postgres",
        supabase_url="https://exampleprojectref.supabase.co",
        allowed_user_id=USER_ID,
        allowed_email="eahtree@gmail.com",
    )
    app = create_app(settings, lifespan=no_lifespan)
    app.state.jwt_validator = FakeValidator()

    async def override() -> FakeFactStore:
        return store

    app.dependency_overrides[get_fact_store] = override
    app.dependency_overrides[get_resume_rebuilder] = lambda: rebuilder
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api_client:
        yield api_client


@pytest.mark.parametrize(
    ("value_type", "value", "expected"),
    [
        ("text", "  Computer Science ", "Computer Science"),
        # None is a fact: the empty value of its type leaves the field blank on purpose.
        ("text", "", ""),
        ("list", [], []),
        ("choice", "hybrid", "hybrid"),
        ("boolean", False, False),
        ("number", 3.78, 3.78),
        ("number", 4, 4),
        ("date", "2027-12-15", "2027-12-15"),
        ("list", ["White"], ["White"]),
        ("object", {"count": 4, "as_of": "2026-08-01"}, {"count": 4, "as_of": "2026-08-01"}),
    ],
)
def test_fact_values_matching_their_type_are_accepted(
    value_type: str, value: object, expected: object
) -> None:
    assert validate_fact_value(value_type, value) == expected


@pytest.mark.parametrize(
    ("value_type", "value"),
    [
        ("choice", ""),
        ("text", 4),
        ("boolean", "true"),
        ("number", True),
        ("number", "40"),
        ("date", "December 2027"),
        ("date", "2027-13-01"),
        ("list", "White"),
        ("object", ["count", 4]),
    ],
)
def test_fact_values_that_do_not_match_their_type_are_rejected(
    value_type: str, value: object
) -> None:
    with pytest.raises(ValueError):
        validate_fact_value(value_type, value)


async def test_a_new_fact_needs_a_label_and_type(client: httpx.AsyncClient) -> None:
    response = await client.put(
        "/v1/jobs/facts/work_auth.security_clearance", json={"value": False}, headers=HEADERS
    )
    assert response.status_code == 422

    response = await client.put(
        "/v1/jobs/facts/work_auth.security_clearance",
        json={"value": False, "label": "Holds a security clearance", "value_type": "boolean"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["value"] is False
    assert body["verified_at"] is not None


async def test_fact_keys_too_long_for_change_history_are_rejected_before_writing(
    client: httpx.AsyncClient, store: FakeFactStore,
) -> None:
    key = "custom." + "x" * 194
    response = await client.put(
        f"/v1/jobs/facts/{key}", headers=HEADERS,
        json={"value": "value", "label": "Example", "value_type": "text"},
    )
    assert response.status_code == 422
    assert store.rows == {}


async def test_updating_a_fact_keeps_its_label_and_type(
    client: httpx.AsyncClient, store: FakeFactStore
) -> None:
    await client.put(
        "/v1/jobs/facts/contact.phone",
        json={"value": "555-0100", "label": "Phone number", "value_type": "text",
              "verified": False},
        headers=HEADERS,
    )
    assert store.rows["contact.phone"]["verified_at"] is None

    response = await client.put(
        "/v1/jobs/facts/contact.phone", json={"value": "555-0199"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert store.rows["contact.phone"] | {"verified_at": None, "updated_at": None} == {
        "key": "contact.phone",
        "label": "Phone number",
        "value": "555-0199",
        "value_type": "text",
        "verified_at": None,
        "updated_at": None,
    }


async def test_a_value_of_the_wrong_type_is_rejected(
    client: httpx.AsyncClient, store: FakeFactStore
) -> None:
    await client.put(
        "/v1/jobs/facts/education.start_date",
        json={"value": "2024-08-19", "label": "When you started your degree",
              "value_type": "date"},
        headers=HEADERS,
    )
    response = await client.put(
        "/v1/jobs/facts/education.start_date",
        json={"value": "May 2028"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert store.rows["education.start_date"]["value"] == "2024-08-19"


@pytest.mark.parametrize("key", ["Education.gpa", "gpa", "education..gpa", "education.gpa-2"])
async def test_malformed_keys_are_rejected(client: httpx.AsyncClient, key: str) -> None:
    response = await client.put(
        f"/v1/jobs/facts/{key}",
        json={"value": 3.78, "label": "GPA", "value_type": "number"},
        headers=HEADERS,
    )
    assert response.status_code == 422


async def test_verify_and_delete(client: httpx.AsyncClient, store: FakeFactStore) -> None:
    assert (await client.post("/v1/jobs/facts/eeo.veteran_status/verify",
                              headers=HEADERS)).status_code == 404
    await client.put(
        "/v1/jobs/facts/eeo.veteran_status",
        json={"value": "Not a veteran", "label": "Veteran status", "value_type": "text",
              "verified": False},
        headers=HEADERS,
    )
    verified = await client.post("/v1/jobs/facts/eeo.veteran_status/verify", headers=HEADERS)
    assert verified.status_code == 200
    assert verified.json()["verified_at"] is not None

    listed = await client.get("/v1/jobs/facts", headers=HEADERS)
    assert [fact["key"] for fact in listed.json()] == ["eeo.veteran_status"]

    assert (await client.delete("/v1/jobs/facts/eeo.veteran_status",
                                headers=HEADERS)).status_code == 204
    assert (await client.delete("/v1/jobs/facts/eeo.veteran_status",
                                headers=HEADERS)).status_code == 404


READ_ONLY_REFUSALS = {
    "education.gpa": "education.gpa comes from the career record; "
    "edit the record at /resume/edit or with os hunt resume put",
    "experience.years_professional": "experience.years_professional is computed "
    "from the career record; edit the record at /resume/edit or with "
    "os hunt resume put",
    "education.graduation_term": "education.graduation_term is computed from "
    "education.graduation_date; edit education.graduation_date in the career "
    "record at /resume/edit",
    "identity.first_name": "identity.first_name is computed from "
    "identity.legal_name; set identity.legal_name",
    "education.highest_completed": "education.highest_completed is computed from "
    "education.degree_level, education.currently_enrolled; edit "
    "education.degree_type, education.graduation_date in the career record "
    "at /resume/edit",
    "availability.conflicts_with_school": "availability.conflicts_with_school is "
    "computed from availability.internship_dates, education.graduation_date; "
    "edit education.graduation_date in the career record at /resume/edit; "
    "set availability.notice_period_weeks",
    "availability.term": "availability.term is computed from the posting's title; "
    "it is decided for each application",
    "referral.referrer_name": "referral.referrer_name is set for one application "
    "with os hunt apply --referred NAME",
}


@pytest.mark.parametrize(("key", "expected"), list(READ_ONLY_REFUSALS.items()))
def test_read_only_refusal_names_what_to_change(key: str, expected: str) -> None:
    assert read_only_refusal(key) == expected


@pytest.mark.parametrize("key", ["contact.phone", "custom.anything"])
def test_read_only_refusal_leaves_keys_it_does_not_own(key: str) -> None:
    assert read_only_refusal(key) is None


async def test_a_read_only_fact_cannot_be_set(
    client: httpx.AsyncClient, store: FakeFactStore
) -> None:
    response = await client.put(
        "/v1/jobs/facts/education.gpa",
        json={"value": 3.78, "label": "GPA", "value_type": "number"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == READ_ONLY_REFUSALS["education.gpa"]
    assert store.rows == {}

    response = await client.put(
        "/v1/jobs/facts/identity.first_name",
        json={"value": "Eah", "label": "First name", "value_type": "text"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == READ_ONLY_REFUSALS["identity.first_name"]
    assert store.rows == {}


async def test_a_read_only_fact_cannot_be_verified_but_a_leftover_row_can_be_deleted(
    client: httpx.AsyncClient, store: FakeFactStore
) -> None:
    now = datetime.now(UTC)
    store.rows["education.gpa"] = {
        "key": "education.gpa",
        "label": "GPA",
        "value": 3.9,
        "value_type": "number",
        "verified_at": None,
        "updated_at": now,
    }
    response = await client.post("/v1/jobs/facts/education.gpa/verify", headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["detail"] == READ_ONLY_REFUSALS["education.gpa"]

    assert (await client.delete("/v1/jobs/facts/education.gpa",
                                headers=HEADERS)).status_code == 204
    assert store.rows == {}


async def test_read_only_facts_are_listed(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/jobs/facts/read-only", headers=HEADERS)
    assert response.status_code == 200
    items = response.json()
    assert len(items) == len(READ_ONLY_FACTS) == 53
    assert {
        "key": "education.gpa",
        "label": "GPA",
        "source": "career",
        "kind": "static",
        "reads": ["career.education"],
    } in items
    assert {
        "key": "education.graduation_term",
        "label": "Graduation term",
        "source": "derived",
        "kind": "computed",
        "reads": ["education.graduation_date"],
    } in items
    assert sum(item["kind"] == "static" for item in items) == 14


def _row(key: str, value: Any, value_type: str = "text") -> dict[str, Any]:
    return {
        "key": key, "label": key, "value": value, "value_type": value_type,
        "verified_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }


async def test_setting_a_header_fact_rebuilds_the_resume(
    client: httpx.AsyncClient, store: FakeFactStore, rebuilder: FakeRebuilder
) -> None:
    store.rows["contact.email"] = _row("contact.email", "old@example.com")
    store.rows["contact.phone"] = _row("contact.phone", "111")
    response = await client.put(
        "/v1/jobs/facts/contact.phone", json={"value": "222"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert rebuilder.headers == [
        {"contact.email": "old@example.com", "contact.phone": "222"}
    ]
    assert rebuilder.ran
    assert store.rows["contact.phone"]["value"] == "222"


async def test_the_same_header_value_rebuilds_nothing(
    client: httpx.AsyncClient, store: FakeFactStore, rebuilder: FakeRebuilder
) -> None:
    store.rows["contact.phone"] = _row("contact.phone", "111")
    response = await client.put(
        "/v1/jobs/facts/contact.phone", json={"value": "111"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert rebuilder.headers == [] and not rebuilder.ran


async def test_a_non_header_fact_never_rebuilds(
    client: httpx.AsyncClient, rebuilder: FakeRebuilder
) -> None:
    response = await client.put(
        "/v1/jobs/facts/contact.phone_extension",
        json={"value": "5", "label": "Extension", "value_type": "text"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert rebuilder.headers == []


async def test_a_failed_build_refuses_the_write(
    client: httpx.AsyncClient, store: FakeFactStore, rebuilder: FakeRebuilder
) -> None:
    rebuilder.build_error = PdfError("no resume")
    response = await client.put(
        "/v1/jobs/facts/contact.phone",
        json={"value": "1", "label": "Phone", "value_type": "text"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "no resume"
    assert "contact.phone" not in store.rows


async def test_a_changed_record_rolls_the_write_back(
    client: httpx.AsyncClient, store: FakeFactStore, rebuilder: FakeRebuilder
) -> None:
    store.rows["contact.phone"] = _row("contact.phone", "111")
    rebuilder.after_error = ResumeConflict()
    response = await client.put(
        "/v1/jobs/facts/contact.phone", json={"value": "222"}, headers=HEADERS
    )
    assert response.status_code == 409
    assert store.rows["contact.phone"]["value"] == "111"


async def test_a_failed_upload_saves_neither(
    client: httpx.AsyncClient, store: FakeFactStore, rebuilder: FakeRebuilder
) -> None:
    store.rows["contact.phone"] = _row("contact.phone", "111")
    rebuilder.after_error = httpx.ConnectError("down")
    response = await client.put(
        "/v1/jobs/facts/contact.phone", json={"value": "222"}, headers=HEADERS
    )
    assert response.status_code == 503
    assert store.rows["contact.phone"]["value"] == "111"


async def test_deleting_a_header_fact_rebuilds_without_it(
    client: httpx.AsyncClient, store: FakeFactStore, rebuilder: FakeRebuilder
) -> None:
    store.rows["contact.github_url"] = _row("contact.github_url", "gh")
    store.rows["contact.email"] = _row("contact.email", "e@example.com")
    response = await client.delete("/v1/jobs/facts/contact.github_url", headers=HEADERS)
    assert response.status_code == 204
    assert rebuilder.headers == [{"contact.email": "e@example.com"}]
    assert "contact.github_url" not in store.rows


async def test_a_failed_delete_build_keeps_the_row(
    client: httpx.AsyncClient, store: FakeFactStore, rebuilder: FakeRebuilder
) -> None:
    store.rows["contact.github_url"] = _row("contact.github_url", "gh")
    rebuilder.build_error = PdfError("no resume")
    response = await client.delete("/v1/jobs/facts/contact.github_url", headers=HEADERS)
    assert response.status_code == 422
    assert store.rows["contact.github_url"]["value"] == "gh"


async def test_no_resume_writes_the_fact_alone(
    client: httpx.AsyncClient, store: FakeFactStore, rebuilder: FakeRebuilder
) -> None:
    rebuilder.returns_none = True
    response = await client.put(
        "/v1/jobs/facts/contact.phone",
        json={"value": "1", "label": "Phone", "value_type": "text"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert store.rows["contact.phone"]["value"] == "1"


class FakeCareerStore:
    def __init__(self, record: Any) -> None:
        self.record = record

    async def get_record(self, user_id: UUID) -> Any:
        return self.record


class FakeCompiler:
    def __init__(self) -> None:
        self.printed_arg: Any = None

    def printed_document(self, document: Any) -> Any:
        self.printed_arg = document
        return document

    def build_resume_pdf(self, printed: Any) -> bytes:
        return b"%PDF"


class FakeStorage:
    def __init__(self) -> None:
        self.uploads: list[bytes] = []

    async def upload_resume(self, user_id: UUID, content: bytes) -> None:
        self.uploads.append(content)


class _Conn:
    async def execute(self, *args: Any) -> "_Conn":
        return self


def _record() -> Any:
    from personal_os_backend.jobs_career_document import (
        CareerRecord,
        EducationRow,
    )

    school = EducationRow.model_validate({
        "school": "Example University", "location": "Atlanta, GA", "level": "university",
        "is_primary": True, "on_resume": True, "degree_type": "BS",
        "field_of_study": "CS", "concentrations": None, "coursework": [],
        "graduation_date": "2027-12-15", "gpa": None, "gpa_scale": None,
    })
    return CareerRecord(education=school, schools=[school], entries=[], skills=[], document={})


async def test_the_rebuilder_builds_and_uploads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_os_backend.jobs_career_document import document_from_rows
    from personal_os_backend.jobs_career_text import contact_from_facts

    record = _record()
    compiler, storage = FakeCompiler(), FakeStorage()
    rebuilder = PostgresResumeRebuilder(FakeCareerStore(record), compiler, storage)
    after = await rebuilder.prepare(USER_ID, {"contact.phone": "5550123456"})
    assert after is not None
    assert compiler.printed_arg["contact"]["phone"] == "555-012-3456"
    # The re-read record renders the same document the build used.
    record.document = document_from_rows(
        record.schools, record.entries, record.skills,
        contact_from_facts({"contact.phone": "5550123456"}),
    )
    monkeypatch.setattr(
        "personal_os_backend.jobs_career.read_record",
        lambda *args: _async_return(record),
    )
    await after(_Conn())
    assert storage.uploads == [b"%PDF"]


async def _async_return(value: Any) -> Any:
    return value


async def test_the_rebuilder_raises_when_the_record_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record()
    compiler, storage = FakeCompiler(), FakeStorage()
    rebuilder = PostgresResumeRebuilder(FakeCareerStore(record), compiler, storage)
    after = await rebuilder.prepare(USER_ID, {"contact.phone": "5550123456"})
    assert after is not None
    record.document = {"contact": {"phone": "changed meanwhile"}}
    monkeypatch.setattr(
        "personal_os_backend.jobs_career.read_record",
        lambda *args: _async_return(record),
    )
    with pytest.raises(ResumeConflict):
        await after(_Conn())
    assert storage.uploads == []


async def test_the_rebuilder_returns_none_without_a_record() -> None:
    rebuilder = PostgresResumeRebuilder(FakeCareerStore(None), FakeCompiler(), FakeStorage())
    assert await rebuilder.prepare(USER_ID, {}) is None


class _GapConnection:
    """Records each statement; the select finds `existing`, the upsert returns `saved`."""

    def __init__(self, existing: dict[str, Any] | None, saved: dict[str, Any]) -> None:
        self.existing = existing
        self.saved = saved
        self.statements: list[tuple[str, Any]] = []
        self._row: dict[str, Any] | None = None

    async def execute(self, query: str, params: Any = None) -> "_GapConnection":
        self.statements.append((query, params))
        self._row = self.existing if query.lstrip().startswith("select") else self.saved
        return self

    async def fetchone(self) -> dict[str, Any] | None:
        return self._row

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    def gap_updates(self) -> list[Any]:
        return [params for query, params in self.statements
                if "update app.jobs_autofill_failure_issues" in query]


class _GapPool:
    def __init__(self, connection: _GapConnection) -> None:
        self._connection = connection

    @asynccontextmanager
    async def connection(self, timeout: float) -> AsyncIterator[_GapConnection]:
        yield self._connection


@pytest.mark.parametrize("stored", ["Fulton", "Cobb", None], ids=["unchanged", "changed", "new"])
async def test_saving_a_fact_settles_the_gap_it_answers(
    monkeypatch: pytest.MonkeyPatch, stored: str | None
) -> None:
    """A re-save of the same value settles it too: that write returns early."""

    async def no_change(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr("personal_os_backend.jobs_facts._record_fact", no_change)
    issue_id = UUID("7d1f7f1e-2f0b-4d57-9a53-0f6c1d3f8a11")
    saved = _row("address.county", "Fulton")
    existing = None if stored is None else _row("address.county", stored)
    connection = _GapConnection(existing, saved)
    store = PostgresFactStore(_GapPool(connection), 5)  # type: ignore[arg-type]

    await store.set_fact(
        USER_ID, "address.county",
        FactWrite(value="Fulton", label=saved["label"], value_type="text",
                  failure=Failure(issue_id=issue_id)),
    )

    assert connection.gap_updates() == [(USER_ID, issue_id, "address.county")]


async def test_a_fact_saved_with_no_failure_still_settles_gaps_naming_its_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_change(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr("personal_os_backend.jobs_facts._record_fact", no_change)
    saved = _row("address.county", "Fulton")
    connection = _GapConnection(None, saved)
    store = PostgresFactStore(_GapPool(connection), 5)  # type: ignore[arg-type]

    await store.set_fact(
        USER_ID, "address.county", FactWrite(value="Fulton", label="County", value_type="text")
    )

    assert connection.gap_updates() == [(USER_ID, None, "address.county")]
