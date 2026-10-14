import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

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
    parse_range,
    render_range,
    rows_from_document,
    split_title,
)
from personal_os_backend.main import create_app
from personal_os_backend.models import Principal

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
HEADERS = {"Authorization": "Bearer approved"}
CANONICAL = Path(__file__).resolve().parents[2] / "artifacts" / "career" / "main.json"
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
    """Import then export reproduces main.json, less the dropped `meta`."""
    original = canonical()
    education, entries, skills = rows_from_document(CareerDocument.model_validate(original))
    rendered = document_from_rows(education, entries, skills, contact_from_facts(FACTS))

    original.pop("meta", None)
    assert rendered == original
    assert {entry.employment_type for entry in entries if entry.section == "experience"} == {
        "coop", "founder", "contract"
    }
    assert education.degree_type == "BS" and education.field_of_study == "Computer Science"


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
        ({"title": "Engineer \u2014 Northwind"}, "em dash"),
        ({"bullets": []}, "at least 1"),
    ],
)
def test_the_resume_rules_are_enforced_on_write(change: dict[str, Any], message: str) -> None:
    document = canonical()
    document["experience"][0] = {**document["experience"][0], **change}
    with pytest.raises(ValidationError, match=message):
        CareerDocument.model_validate(document)


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeValidator:
    async def verify(self, token: str) -> Principal:
        return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", None)


class FakeCareerStore:
    def __init__(self) -> None:
        self.record: CareerRecord | None = None

    async def get_record(self, user_id: UUID) -> CareerRecord | None:
        return self.record

    async def write_record(
        self, user_id: UUID, document: CareerDocument, *, only_if_empty: bool
    ) -> CareerRecord | None:
        if only_if_empty and self.record is not None:
            return None
        derived = contact_from_facts(FACTS)
        conflicts = contact_conflicts(document, derived)
        if conflicts:
            raise CareerError("; ".join(conflicts))
        education, entries, skills = rows_from_document(document)
        self.record = CareerRecord(
            education=education,
            entries=entries,
            skills=skills,
            document=document_from_rows(education, entries, skills, derived),
        )
        return self.record


@pytest.fixture
def store() -> FakeCareerStore:
    return FakeCareerStore()


@pytest.fixture
def client(store: FakeCareerStore) -> httpx.AsyncClient:
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

    async def override() -> FakeCareerStore:
        return store

    app.dependency_overrides[get_career_store] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_import_once_then_read_and_replace(client: httpx.AsyncClient) -> None:
    async with client:
        missing = await client.get("/v1/jobs/career", headers=HEADERS)
        assert missing.status_code == 404

        imported = await client.post("/v1/jobs/career/import", headers=HEADERS, json=canonical())
        assert imported.status_code == 201
        assert imported.json()["education"]["graduation_date"] == "2027-12-01"

        again = await client.post("/v1/jobs/career/import", headers=HEADERS, json=canonical())
        assert again.status_code == 409

        edited = canonical()
        edited["skills"][0]["items"] = "Python, Go"
        replaced = await client.put("/v1/jobs/career", headers=HEADERS, json=edited)
        assert replaced.status_code == 200
        assert replaced.json()["document"]["skills"][0]["items"] == "Python, Go"

        edited["contact"]["email"] = "someone@example.com"
        refused = await client.put("/v1/jobs/career", headers=HEADERS, json=edited)
        assert refused.status_code == 422
        assert "contact.email" in refused.json()["detail"]
