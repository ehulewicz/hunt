from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from personal_os_backend.config import Settings
from personal_os_backend.jobs_devices import (
    DeviceHeartbeat,
    UnknownRun,
    get_device_store,
    is_online,
)
from personal_os_backend.main import create_app
from personal_os_backend.models import Principal

USER_ID = UUID("c3c8d239-162d-4e5e-a2a1-4ac0d1b3695f")
DEVICE_ID = UUID("33333333-3333-4333-8333-333333333333")
RUN_ID = UUID("44444444-4444-4444-8444-444444444444")
HEADERS = {"Authorization": "Bearer approved"}
HEARTBEAT = {
    "name": "DESKTOP-HOME",
    "url": "https://desktop-home.tail1234.ts.net",
    "commit": "4c1946d0a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "local_changes": False,
    "ready": True,
    "problem": None,
    "active_run_id": None,
}


@asynccontextmanager
async def no_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


class FakeValidator:
    async def verify(self, token: str) -> Principal:
        return Principal(USER_ID, "eahtree@gmail.com", "Eah Tree", None)


class FakeDeviceStore:
    """Mirrors PostgresDeviceStore: one row per id, owned by whoever wrote it first."""

    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, Any]] = {}
        self.owners: dict[UUID, UUID] = {}
        self.runs = {RUN_ID: ("Acme", "Software Engineer Intern")}
        self.seen_users: list[UUID] = []

    async def list_devices(self, user_id: UUID) -> list[dict[str, Any]]:
        self.seen_users.append(user_id)
        return [
            row for key, row in sorted(self.rows.items(), key=lambda item: item[1]["name"])
            if self.owners[key] == user_id
        ]

    async def record_heartbeat(
        self, user_id: UUID, device_id: UUID, heartbeat: DeviceHeartbeat
    ) -> dict[str, Any] | None:
        self.seen_users.append(user_id)
        if heartbeat.active_run_id is not None and heartbeat.active_run_id not in self.runs:
            raise UnknownRun("active_run_id is not one of your runs")
        if self.owners.setdefault(device_id, user_id) != user_id:
            return None
        company, title = self.runs.get(heartbeat.active_run_id, (None, None)) \
            if heartbeat.active_run_id else (None, None)
        self.rows[device_id] = {
            "id": device_id,
            **heartbeat.model_dump(),
            "last_seen_at": datetime.now(UTC),
            "run_company_name": company,
            "run_title": title,
        }
        return self.rows[device_id]


@pytest.fixture
def store() -> FakeDeviceStore:
    return FakeDeviceStore()


@pytest.fixture
async def client(store: FakeDeviceStore) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url="postgresql://postgres:password@localhost:5432/postgres",
        supabase_url="https://exampleprojectref.supabase.co",
        allowed_user_id=USER_ID,
        allowed_email="eahtree@gmail.com",
    )
    app = create_app(settings, lifespan=no_lifespan)
    app.state.jwt_validator = FakeValidator()

    async def override() -> FakeDeviceStore:
        return store

    app.dependency_overrides[get_device_store] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api_client:
        yield api_client


async def test_a_heartbeat_upserts_the_device_for_the_signed_in_user(
    client: httpx.AsyncClient, store: FakeDeviceStore
) -> None:
    response = await client.put(f"/v1/jobs/devices/{DEVICE_ID}", json=HEARTBEAT, headers=HEADERS)
    assert response.status_code == 200
    device = response.json()
    assert device["id"] == str(DEVICE_ID)
    assert device["online"] is True
    assert device["run"] is None
    assert store.seen_users == [USER_ID]

    again = await client.put(
        f"/v1/jobs/devices/{DEVICE_ID}",
        json=HEARTBEAT | {"local_changes": True, "active_run_id": str(RUN_ID)},
        headers=HEADERS,
    )
    assert again.status_code == 200
    assert again.json()["run"] == {"company_name": "Acme", "title": "Software Engineer Intern"}
    listed = (await client.get("/v1/jobs/devices", headers=HEADERS)).json()
    assert [item["id"] for item in listed] == [str(DEVICE_ID)]
    assert listed[0]["local_changes"] is True


async def test_the_user_never_comes_from_the_body(client: httpx.AsyncClient) -> None:
    response = await client.put(
        f"/v1/jobs/devices/{DEVICE_ID}",
        json=HEARTBEAT | {"user_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        headers=HEADERS,
    )
    assert response.status_code == 422


async def test_a_device_needs_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/jobs/devices")).status_code == 401
    assert (
        await client.put(f"/v1/jobs/devices/{DEVICE_ID}", json=HEARTBEAT)
    ).status_code == 401


@pytest.mark.parametrize(
    "url",
    [
        "http://desktop-home.tail1234.ts.net",
        "https://evil.example.com",
        "https://desktop-home.tail1234.ts.net.evil.com",
        "https://desktop-home.tail1234.ts.net/path",
        "https://user@desktop-home.tail1234.ts.net",
    ],
)
async def test_the_address_must_be_a_tailnet_https_name(
    client: httpx.AsyncClient, url: str
) -> None:
    response = await client.put(
        f"/v1/jobs/devices/{DEVICE_ID}", json=HEARTBEAT | {"url": url}, headers=HEADERS
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("ready", "problem"), [(True, "Chrome CDP: ConnectError"), (False, None)]
)
async def test_not_ready_and_a_problem_go_together(
    client: httpx.AsyncClient, ready: bool, problem: str | None
) -> None:
    response = await client.put(
        f"/v1/jobs/devices/{DEVICE_ID}",
        json=HEARTBEAT | {"ready": ready, "problem": problem},
        headers=HEADERS,
    )
    assert response.status_code == 422


async def test_a_run_that_is_not_yours_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.put(
        f"/v1/jobs/devices/{DEVICE_ID}",
        json=HEARTBEAT | {"active_run_id": "55555555-5555-4555-8555-555555555555"},
        headers=HEADERS,
    )
    assert response.status_code == 422


async def test_an_id_another_user_holds_is_a_conflict(
    client: httpx.AsyncClient, store: FakeDeviceStore
) -> None:
    store.owners[DEVICE_ID] = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    response = await client.put(f"/v1/jobs/devices/{DEVICE_ID}", json=HEARTBEAT, headers=HEADERS)
    assert response.status_code == 409


def test_online_means_a_heartbeat_in_the_last_90_seconds() -> None:
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    assert is_online(now - timedelta(seconds=89), now)
    assert not is_online(now - timedelta(seconds=90), now)
