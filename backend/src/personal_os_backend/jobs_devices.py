"""Devices: the machines that run the Hunt agent, and whether each one is up.

A device lists itself. Its agent sends a heartbeat every 30 seconds through the
device's local API, the same path every worker write takes, and the page reads
the list from here to know which devices it can connect to. A device is online
while its last heartbeat is under 90 seconds old. The id is the device's own,
generated once and kept on the machine; the user is always the verified JWT's.
Nothing here carries the stream: the page connects to the device's tailnet
address directly.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Protocol, Self
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from personal_os_backend.auth import CurrentPrincipal
from personal_os_backend.config import Settings

ONLINE_WITHIN = timedelta(seconds=90)
# The page sends the user's access token to this address, so it must be a
# tailnet name served over HTTPS by `tailscale serve`, never an arbitrary host.
TAILNET_URL = re.compile(r"^https://[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9-]+)*\.ts\.net$")
DEVICE_COLUMNS = (
    "device.id, device.name, device.url, device.commit, device.local_changes, device.ready, "
    "device.problem, device.active_run_id, device.last_seen_at, "
    "company.name as run_company_name, posting.title as run_title"
)
DEVICE_JOINS = """
from app.jobs_devices device
left join app.jobs_autofill_runs run on run.id = device.active_run_id
left join app.jobs_applications application on application.id = run.application_id
left join app.jobs_postings posting on posting.id = application.posting_id
left join app.jobs_companies company on company.id = posting.company_id
"""


class DeviceHeartbeat(BaseModel):
    """What a device says about itself every 30 seconds."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    url: str = Field(max_length=300)
    commit: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    local_changes: bool
    ready: bool
    problem: str | None = Field(default=None, min_length=1, max_length=500)
    active_run_id: UUID | None = None

    @model_validator(mode="after")
    def checked(self) -> Self:
        if not TAILNET_URL.fullmatch(self.url):
            raise ValueError("url must be the device's https://<name>.ts.net address")
        if self.ready != (self.problem is None):
            raise ValueError("a device that is not ready names its problem, and only then")
        return self


class DeviceRun(BaseModel):
    company_name: str
    title: str


class Device(BaseModel):
    id: UUID
    name: str
    url: str
    commit: str
    local_changes: bool
    ready: bool
    problem: str | None
    active_run_id: UUID | None
    run: DeviceRun | None
    last_seen_at: datetime
    online: bool


def is_online(last_seen_at: datetime, now: datetime) -> bool:
    return now - last_seen_at < ONLINE_WITHIN


def device_from_row(row: dict[str, Any], now: datetime) -> Device:
    run = None
    if row.get("active_run_id") is not None and row.get("run_company_name"):
        run = DeviceRun(company_name=row["run_company_name"], title=row.get("run_title") or "")
    return Device(
        id=row["id"],
        name=row["name"],
        url=row["url"],
        commit=row["commit"],
        local_changes=row["local_changes"],
        ready=row["ready"],
        problem=row["problem"],
        active_run_id=row["active_run_id"],
        run=run,
        last_seen_at=row["last_seen_at"],
        online=is_online(row["last_seen_at"], now),
    )


class DeviceStore(Protocol):
    async def list_devices(self, user_id: UUID) -> list[dict[str, Any]]: ...

    async def record_heartbeat(
        self, user_id: UUID, device_id: UUID, heartbeat: DeviceHeartbeat
    ) -> dict[str, Any] | None: ...


class UnknownRun(ValueError):
    """The heartbeat names a run that is not this user's."""


class PostgresDeviceStore:
    def __init__(self, pool: AsyncConnectionPool[Any], timeout: float) -> None:
        self._pool = pool
        self._timeout = timeout

    async def list_devices(self, user_id: UUID) -> list[dict[str, Any]]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"select {DEVICE_COLUMNS} {DEVICE_JOINS} "
                "where device.user_id = %s order by device.name, device.id",
                (user_id,),
            )
            return list(await cursor.fetchall())

    async def record_heartbeat(
        self, user_id: UUID, device_id: UUID, heartbeat: DeviceHeartbeat
    ) -> dict[str, Any] | None:
        """Upsert the device; None when the id already belongs to another user."""
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            if heartbeat.active_run_id is not None:
                cursor = await connection.execute(
                    "select 1 from app.jobs_autofill_runs where id = %s and user_id = %s",
                    (heartbeat.active_run_id, user_id),
                )
                if await cursor.fetchone() is None:
                    raise UnknownRun("active_run_id is not one of your runs")
            cursor = await connection.execute(
                """
                insert into app.jobs_devices (
                  id, user_id, name, url, commit, local_changes, ready, problem,
                  active_run_id, last_seen_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                on conflict (id) do update set
                  name = excluded.name, url = excluded.url, commit = excluded.commit,
                  local_changes = excluded.local_changes, ready = excluded.ready,
                  problem = excluded.problem, active_run_id = excluded.active_run_id,
                  last_seen_at = now()
                where app.jobs_devices.user_id = excluded.user_id
                returning id
                """,
                (
                    device_id,
                    user_id,
                    heartbeat.name.strip(),
                    heartbeat.url,
                    heartbeat.commit,
                    heartbeat.local_changes,
                    heartbeat.ready,
                    heartbeat.problem,
                    heartbeat.active_run_id,
                ),
            )
            if await cursor.fetchone() is None:
                return None
            cursor = await connection.execute(
                f"select {DEVICE_COLUMNS} {DEVICE_JOINS} where device.id = %s",
                (device_id,),
            )
            row: dict[str, Any] | None = await cursor.fetchone()
            return row


async def get_device_store(request: Request) -> DeviceStore:
    settings: Settings = request.app.state.settings
    return PostgresDeviceStore(request.app.state.pool, settings.database_pool_timeout_seconds)


router = APIRouter(prefix="/v1/jobs/devices", tags=["devices"])
Store = Annotated[DeviceStore, Depends(get_device_store)]


@router.get("", response_model=list[Device])
async def list_devices(principal: CurrentPrincipal, store: Store) -> list[Device]:
    now = datetime.now(UTC)
    return [device_from_row(row, now) for row in await store.list_devices(principal.user_id)]


@router.put("/{device_id}", response_model=Device)
async def record_heartbeat(
    device_id: UUID, heartbeat: DeviceHeartbeat, principal: CurrentPrincipal, store: Store
) -> Device:
    try:
        row = await store.record_heartbeat(principal.user_id, device_id, heartbeat)
    except UnknownRun as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That device id is taken")
    return device_from_row(row, datetime.now(UTC))
