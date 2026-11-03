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
from personal_os_backend.jobs_facts import (
    Fact,
    FactWrite,
    get_fact_store,
    validate_fact_value,
)
from personal_os_backend.jobs_facts_computed import COMPUTED_FACTS, computed_refusal
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

    async def set_fact(self, user_id: UUID, key: str, item: FactWrite) -> Fact:
        existing = self.rows.get(key, {})
        label = item.label or existing.get("label")
        value_type = item.value_type or existing.get("value_type")
        if not label or not value_type:
            raise ValueError("A new fact needs a label and a value_type")
        now = datetime.now(UTC)
        self.rows[key] = {
            "key": key,
            "label": label,
            "value": validate_fact_value(value_type, item.value),
            "value_type": value_type,
            "verified_at": now if item.verified else None,
            "updated_at": now,
        }
        return Fact.model_validate(self.rows[key])

    async def verify_fact(self, user_id: UUID, key: str) -> Fact | None:
        if key not in self.rows:
            return None
        self.rows[key]["verified_at"] = datetime.now(UTC)
        return Fact.model_validate(self.rows[key])

    async def delete_fact(self, user_id: UUID, key: str) -> bool:
        return self.rows.pop(key, None) is not None


@pytest.fixture
def store() -> FakeFactStore:
    return FakeFactStore()


@pytest.fixture
async def client(store: FakeFactStore) -> AsyncIterator[httpx.AsyncClient]:
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


COMPUTED_REFUSALS = {
    "education.gpa": "education.gpa is computed from the career record; "
    "edit the record at /resume/edit or with os hunt resume put",
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


@pytest.mark.parametrize(("key", "expected"), list(COMPUTED_REFUSALS.items()))
def test_computed_refusal_names_what_to_change(key: str, expected: str) -> None:
    assert computed_refusal(key) == expected


@pytest.mark.parametrize("key", ["contact.phone", "custom.anything"])
def test_computed_refusal_leaves_keys_it_does_not_own(key: str) -> None:
    assert computed_refusal(key) is None


async def test_a_computed_fact_cannot_be_set(
    client: httpx.AsyncClient, store: FakeFactStore
) -> None:
    response = await client.put(
        "/v1/jobs/facts/education.gpa",
        json={"value": 3.78, "label": "GPA", "value_type": "number"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == COMPUTED_REFUSALS["education.gpa"]
    assert store.rows == {}

    response = await client.put(
        "/v1/jobs/facts/identity.first_name",
        json={"value": "Eah", "label": "First name", "value_type": "text"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == COMPUTED_REFUSALS["identity.first_name"]
    assert store.rows == {}


async def test_a_computed_fact_cannot_be_verified_but_a_leftover_row_can_be_deleted(
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
    assert response.json()["detail"] == COMPUTED_REFUSALS["education.gpa"]

    assert (await client.delete("/v1/jobs/facts/education.gpa",
                                headers=HEADERS)).status_code == 204
    assert store.rows == {}


async def test_computed_facts_are_listed_read_only(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/jobs/facts/computed", headers=HEADERS)
    assert response.status_code == 200
    items = response.json()
    assert len(items) == len(COMPUTED_FACTS)
    assert {
        "key": "education.gpa",
        "label": "GPA",
        "source": "career",
        "reads": ["career.education"],
    } in items
