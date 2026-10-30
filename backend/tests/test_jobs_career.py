import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from personal_os_backend.config import Settings
from personal_os_backend.jobs_career import (
    CareerDocument,
    CareerError,
    CareerRecord,
    contact_conflicts,
    contact_from_facts,
    document_from_rows,
    get_career_store,
    parse_location,
    parse_range,
    render_location,
    render_range,
    rows_from_document,
    split_title,
)
from personal_os_backend.jobs_career_pdf import (
    PdfError,
    get_pdf_compiler,
    printed_document,
)
from personal_os_backend.jobs_storage import get_document_storage
from personal_os_backend.main import create_app
from personal_os_backend.models import Principal

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
HEADERS = {"Authorization": "Bearer approved"}
CANONICAL = Path(__file__).resolve().parent / "fixtures" / "career_v2.json"
FACTS = {
    "identity.legal_name": "Alex Example",
    "contact.phone": "5550100199",
    "contact.email": "alex.example@example.com",
    "contact.linkedin_url": "https://www.linkedin.com/in/alex-example/",
    "contact.github_url": "https://github.com/ehulewicz/",
    "work_auth.us_citizen": True,
}


def canonical() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(CANONICAL.read_text(encoding="utf-8"))
    return payload


def test_dates_parse_to_the_first_of_the_month_and_render_back() -> None:
    assert parse_range("May 2025 \u2013 Present") == (date(2025, 5, 1), None)
    assert parse_range("October 2025 - May 2026") == (date(2025, 10, 1), date(2026, 5, 1))
    assert parse_range("June 2026") == (None, date(2026, 6, 1))
    assert parse_range("") == (None, None)
    for text in ("May 2025 \u2013 Present", "October 2025 \u2013 May 2026", "June 2026", ""):
        assert render_range(*parse_range(text)) == text


@pytest.mark.parametrize("text", ["Summer 2025", "May 2026 \u2013 May 2025", "2025"])
def test_a_date_line_that_is_not_months_is_refused(text: str) -> None:
    with pytest.raises(CareerError):
        parse_range(text)


def test_the_employment_type_is_the_title_suffix() -> None:
    assert split_title("experience", "Middleware Engineer (Co-op)") == (
        "Middleware Engineer", "coop"
    )
    assert split_title("experience", "Software Engineer (Contract)") == (
        "Software Engineer", "contract"
    )
    assert split_title("experience", "Technical Co-founder") == ("Technical Co-founder", "founder")
    assert split_title("experience", "Backend Engineer") == ("Backend Engineer", "full_time")
    assert split_title("research", "Sample Lab (Co-op)") == ("Sample Lab (Co-op)", None)
    # An activity never gets an employment type.
    assert split_title("activity", "Robotics Club (Intern)") == ("Robotics Club (Intern)", None)


def test_the_header_is_rendered_from_facts() -> None:
    assert contact_from_facts(FACTS) == {
        "name": "Alex Example",
        "phone": "555-010-0199",
        "email": "alex.example@example.com",
        "linkedin": "https://www.linkedin.com/in/alex-example",
        "github": "https://github.com/ehulewicz",
        "citizenship": "US Citizen",
    }
    assert contact_from_facts({})["citizenship"] == ""


def test_the_canonical_resume_round_trips_through_rows() -> None:
    """Parsing then rendering reproduces the v2 document."""
    original = canonical()
    schools, entries, skills = rows_from_document(CareerDocument.model_validate(original))
    rendered = document_from_rows(schools, entries, skills, contact_from_facts(FACTS))

    assert rendered == original
    assert {entry.employment_type for entry in entries if entry.section == "experience"} == {
        "coop", "founder", "contract"
    }
    primary = next(school for school in schools if school.is_primary)
    assert primary.degree_type == "BS" and primary.field_of_study == "Computer Science"
    assert primary.level == "undergraduate" and len(primary.coursework) == 5


@pytest.mark.parametrize(
    ("text", "parts"),
    [
        ("Atlanta, GA", ("Atlanta", "GA", "United States")),
        ("Metz, France", ("Metz", None, "France")),
        ("Toronto, ON, Canada", ("Toronto", "ON", "Canada")),
        ("Remote", ("Remote", None, None)),
        ("", (None, None, None)),
    ],
)
def test_a_location_splits_into_parts_and_renders_back(
    text: str, parts: tuple[str | None, str | None, str | None]
) -> None:
    assert parse_location(text) == parts
    assert render_location(*parts) == text


def test_research_carries_the_title_and_employer_a_form_gets() -> None:
    _, entries, _ = rows_from_document(CareerDocument.model_validate(canonical()))
    research = [entry for entry in entries if entry.section == "research"]
    assert {entry.job_title for entry in research} == {"Undergraduate Researcher"}
    assert [entry.employer for entry in research] == [entry.title for entry in research]
    assert all(entry.job_title is None for entry in entries if entry.section == "experience")


def test_a_document_with_only_a_location_is_split_into_parts() -> None:
    document = canonical()
    for name in ("city", "region", "country"):
        document["research"][0].pop(name, None)
    _, entries, _ = rows_from_document(CareerDocument.model_validate(document))
    lab = next(entry for entry in entries if entry.section == "research")
    assert (lab.city, lab.region, lab.country) == ("Metz", None, "France")


def test_a_location_that_disagrees_with_its_parts_is_refused() -> None:
    document = canonical()
    document["experience"][0]["location"] = "Austin, TX"
    with pytest.raises(CareerError, match="city, region, and country"):
        rows_from_document(CareerDocument.model_validate(document))


def test_a_header_that_disagrees_with_the_facts_names_the_fact_to_set() -> None:
    document = CareerDocument.model_validate(canonical())
    assert contact_conflicts(document, contact_from_facts(FACTS)) == []

    edited = canonical()
    edited["contact"]["phone"] = "555-000-0000"
    conflicts = contact_conflicts(
        CareerDocument.model_validate(edited), contact_from_facts(FACTS)
    )
    assert len(conflicts) == 1 and "contact.phone" in conflicts[0]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"bullets": ["Shipped a tool; then another"]}, "semicolons"),
        ({"bullets": ["Built **FastAPI** services"]}, "emphasis"),
        ({"moreBullets": ["Kept a note; cut it"]}, "semicolons"),
        ({"title": "Engineer \u2014 Northwind"}, "em dash"),
    ],
)
def test_the_resume_rules_are_enforced_on_write(change: dict[str, Any], message: str) -> None:
    document = canonical()
    document["experience"][0] = {**document["experience"][0], **change}
    with pytest.raises(ValidationError, match=message):
        CareerDocument.model_validate(document)


def test_a_printed_entry_needs_a_bullet_but_an_off_resume_one_may_have_none() -> None:
    document = canonical()
    document["experience"][0]["bullets"] = []
    with pytest.raises(ValidationError, match="at least one bullet"):
        CareerDocument.model_validate(document)
    document["experience"][0]["onResume"] = False
    CareerDocument.model_validate(document)


@pytest.mark.parametrize("primaries", [0, 2])
def test_education_needs_exactly_one_primary_school(primaries: int) -> None:
    document = canonical()
    document["education"].append(dict(document["education"][0]))
    for index, school in enumerate(document["education"]):
        school["primary"] = index < primaries
    if primaries == 0:
        for school in document["education"]:
            school["primary"] = False
    with pytest.raises(ValidationError, match="exactly one primary"):
        CareerDocument.model_validate(document)


@pytest.mark.parametrize(
    "courses",
    [["Valid course", "  "], ["x" * 201], ["ok"] * 31],
)
def test_courses_are_short_non_blank_items(courses: list[str]) -> None:
    document = canonical()
    document["education"][0]["courses"] = courses
    with pytest.raises(ValidationError):
        CareerDocument.model_validate(document)


def test_meta_is_no_longer_accepted() -> None:
    document = canonical() | {"meta": {"name": "main"}}
    with pytest.raises(ValidationError):
        CareerDocument.model_validate(document)


def test_the_projection_prints_only_what_is_on_the_resume() -> None:
    document = canonical()
    document["experience"][0]["id"] = str(uuid4())
    document["experience"][0]["moreBullets"] = ["A kept bullet"]
    document["experience"][1]["onResume"] = False
    document["education"].append(
        {**document["education"][0], "school": "Hidden School", "primary": False,
         "onResume": False}
    )
    printed = printed_document(document)

    assert [school["school"] for school in printed["education"]] == [
        "Example Institute of Technology"
    ]
    assert all(
        key not in printed["education"][0] for key in ("level", "primary", "onResume")
    )
    assert [entry["title"] for entry in printed["experience"]] == [
        "Middleware Engineer (Co-op)", "Software Engineer (Contract)"
    ]
    assert printed["experience"][0]["bullets"] == document["experience"][0]["bullets"]
    assert all(
        key not in printed["experience"][0] for key in ("id", "onResume", "moreBullets")
    )
    assert printed["contact"] == document["contact"]
    assert printed["skills"] == document["skills"]


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeValidator:
    async def verify(self, token: str) -> Principal:
        return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", None)


class FakeCompiler:
    def __init__(self, pdf: bytes = b"%PDF-fake", error: PdfError | None = None) -> None:
        self.pdf = pdf
        self.error = error
        self.printed: dict[str, Any] | None = None

    def printed_document(self, document: dict[str, Any]) -> dict[str, Any]:
        self.printed = printed_document(document)
        return self.printed

    def build_resume_pdf(self, printed: dict[str, Any]) -> bytes:
        if self.error is not None:
            raise self.error
        return self.pdf

    def build_letter_pdf(
        self, contact: dict[str, Any], text: str, *, date: str, company: str
    ) -> bytes:
        if self.error is not None:
            raise self.error
        return self.pdf


class FakeStorage:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.uploads: list[tuple[UUID, bytes]] = []
        self.pdf: bytes | None = None

    async def upload_resume(self, user_id: UUID, content: bytes) -> None:
        if self.failure is not None:
            raise self.failure
        self.uploads.append((user_id, content))
        self.pdf = content

    async def download_resume(self, user_id: UUID) -> bytes | None:
        return self.pdf


class FakeCareerStore:
    """The stable-id write contract, in memory: a named id updates, an absent
    id inserts, an omitted row is deleted, and the upload happens last."""

    def __init__(self) -> None:
        self.record: CareerRecord | None = None
        self.writes = 0

    async def get_record(self, user_id: UUID) -> CareerRecord | None:
        return self.record

    async def write_record(
        self, user_id: UUID, document: CareerDocument, *, rows: Any,
        pdf: bytes, storage: Any
    ) -> CareerRecord | None:
        self.writes += 1
        derived = contact_from_facts(FACTS)
        conflicts = contact_conflicts(document, derived)
        if conflicts:
            raise CareerError("; ".join(conflicts))
        schools, entries, skills = rows
        existing = {entry.id for entry in self.record.entries} if self.record else set()
        seen: set[UUID] = set()
        for entry in entries:
            if entry.id is not None:
                if entry.id in seen:
                    raise CareerError(f"duplicate entry id {entry.id}")
                if entry.id not in existing:
                    raise CareerError(f"unknown entry id {entry.id}")
            else:
                entry.id = uuid4()
            seen.add(entry.id)
        # The upload happens inside the write; a failure leaves the old record.
        await storage.upload_resume(user_id, pdf)
        entries = [entry for entry in entries if entry.id in seen]
        self.record = CareerRecord(
            education=next(school for school in schools if school.is_primary),
            schools=schools,
            entries=entries,
            skills=skills,
            document=document_from_rows(schools, entries, skills, derived),
        )
        return self.record


@pytest.fixture
def store() -> FakeCareerStore:
    return FakeCareerStore()


@pytest.fixture
def compiler() -> FakeCompiler:
    return FakeCompiler()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def client(
    store: FakeCareerStore, compiler: FakeCompiler, storage: FakeStorage
) -> httpx.AsyncClient:
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

    async def override_store() -> FakeCareerStore:
        return store

    async def override_compiler() -> FakeCompiler:
        return compiler

    async def override_storage() -> FakeStorage:
        return storage

    app.dependency_overrides[get_career_store] = override_store
    app.dependency_overrides[get_pdf_compiler] = override_compiler
    app.dependency_overrides[get_document_storage] = override_storage
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_read_write_and_import_is_gone(
    client: httpx.AsyncClient, storage: FakeStorage, compiler: FakeCompiler
) -> None:
    async with client:
        missing = await client.get("/v1/jobs/career", headers=HEADERS)
        assert missing.status_code == 404
        assert "os hunt resume put" in missing.json()["detail"]

        assert (
            await client.post("/v1/jobs/career/import", headers=HEADERS, json=canonical())
        ).status_code in (404, 405)

        written = await client.put("/v1/jobs/career", headers=HEADERS, json=canonical())
        assert written.status_code == 200
        record = written.json()
        assert record["education"]["level"] == "undergraduate"
        assert record["education"]["is_primary"] is True
        assert record["education"]["coursework"][0] == "Data Structures & Algorithms"
        assert len(record["schools"]) == 1
        assert all("id" in entry for entry in record["document"]["experience"])
        assert storage.uploads == [(USER_ID, b"%PDF-fake")]
        # The compiler saw the printed projection, not the document.
        assert compiler.printed is not None
        assert "id" not in compiler.printed["experience"][0]

        edited = canonical()
        edited["skills"][0]["items"] = "Python, Go"
        replaced = await client.put("/v1/jobs/career", headers=HEADERS, json=edited)
        assert replaced.status_code == 200
        assert replaced.json()["document"]["skills"][0]["items"] == "Python, Go"

        edited["contact"]["email"] = "someone@example.com"
        refused = await client.put("/v1/jobs/career", headers=HEADERS, json=edited)
        assert refused.status_code == 422
        assert "contact.email" in refused.json()["detail"]


async def test_entry_ids_are_stable_and_deletions_are_explicit(
    client: httpx.AsyncClient,
) -> None:
    async with client:
        first = await client.put("/v1/jobs/career", headers=HEADERS, json=canonical())
        assert first.status_code == 200
        ids = {
            section: [entry["id"] for entry in first.json()["document"][section]]
            for section in ("experience", "research", "projects")
        }

        again = canonical()
        again["experience"][0]["id"] = ids["experience"][0]
        again["experience"].append(
            {
                "title": "New Role", "org": "Acme", "date": "June 2026",
                "bullets": ["Did a thing"],
            }
        )
        again["research"] = []  # the two research entries are dropped
        second = await client.put("/v1/jobs/career", headers=HEADERS, json=again)
        assert second.status_code == 200
        experience = second.json()["document"]["experience"]
        assert experience[0]["id"] == ids["experience"][0]
        assert second.json()["document"]["research"] == []

        unknown = canonical()
        unknown["experience"][0]["id"] = str(uuid4())
        refused = await client.put("/v1/jobs/career", headers=HEADERS, json=unknown)
        assert refused.status_code == 422
        assert "unknown entry id" in refused.json()["detail"]

        duplicate = canonical()
        duplicate["experience"][0]["id"] = ids["experience"][0]
        duplicate["projects"][0]["id"] = ids["experience"][0]
        refused = await client.put("/v1/jobs/career", headers=HEADERS, json=duplicate)
        assert refused.status_code == 422
        assert "duplicate entry id" in refused.json()["detail"]


async def test_a_build_failure_refuses_the_write_and_stores_nothing(
    client: httpx.AsyncClient,
    store: FakeCareerStore,
    compiler: FakeCompiler,
    storage: FakeStorage,
) -> None:
    compiler.error = PdfError("resume must stay within 1 page(s); generated 2")
    async with client:
        refused = await client.put("/v1/jobs/career", headers=HEADERS, json=canonical())
        assert refused.status_code == 422
        assert "1 page" in refused.json()["detail"]
        assert store.writes == 0 and store.record is None
        assert storage.uploads == []


async def test_a_storage_failure_rolls_the_write_back(
    client: httpx.AsyncClient,
    store: FakeCareerStore,
    storage: FakeStorage,
) -> None:
    storage.failure = httpx.ConnectError("offline")
    async with client:
        refused = await client.put("/v1/jobs/career", headers=HEADERS, json=canonical())
        assert refused.status_code == 503
        assert "could not be stored" in refused.json()["detail"]
        assert store.record is None


async def test_the_compile_uses_the_rendered_document(
    client: httpx.AsyncClient, compiler: FakeCompiler
) -> None:
    """A body giving only city/region/country prints the rendered location."""
    document = canonical()
    document["experience"][0].pop("location", None)
    async with client:
        written = await client.put("/v1/jobs/career", headers=HEADERS, json=document)
        assert written.status_code == 200
    printed = compiler.printed
    assert printed is not None
    assert printed["experience"][0]["location"] == "Atlanta, GA"
    assert printed["experience"][0]["date"] == "May 2025 \u2013 Present"


def test_a_hyphen_date_compiles_as_the_en_dash_get_renders() -> None:
    document = canonical()
    document["experience"][0]["date"] = "May 2025 - Present"
    rows = rows_from_document(CareerDocument.model_validate(document))
    rendered = document_from_rows(*rows, {"name": "x"})
    assert rendered["experience"][0]["date"] == "May 2025 \u2013 Present"
