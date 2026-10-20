"""List this device as up, and say whether it is ready, every 30 seconds.

The heartbeat goes through the local API like every other worker write, so the
hosted side needs nothing new to accept it. Readiness is `os doctor`: the first
failing check is the device's problem. Doctor shells out to Claude, LaTeX, and
gcloud, so it runs every ten minutes, not every beat.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from personal_os_worker.core import service
from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.auth import active_session
from personal_os_worker.core.commands import supabase_auth
from personal_os_worker.core.config import CliConfig
from personal_os_worker.core.doctor import DoctorCheck, run_doctor
from personal_os_worker.core.secrets import secrets_check
from personal_os_worker.device.identity import (
    DeviceError,
    DeviceIdentity,
    save_identity,
    tailnet_url,
)
from personal_os_worker.device.update import Checkout, read_checkout
from personal_os_worker.documents.doctor import CHECKS as DOCUMENT_CHECKS

HEARTBEAT_SECONDS = 30.0
DOCTOR_SECONDS = 600.0
# The agent starts Chrome itself when a page connects or a run starts, so a
# closed automation window is not a reason the device cannot work.
ON_DEMAND = frozenset({"Chrome CDP"})


def readiness(checks: Sequence[DoctorCheck]) -> tuple[bool, str | None]:
    """Ready, or the first failing check that would stop a run here."""
    for check in checks:
        if not check.ok and check.name not in ON_DEMAND:
            return False, f"{check.name}: {check.detail}"[:500]
    return True, None


def heartbeat_payload(
    identity: DeviceIdentity,
    *,
    url: str,
    checkout: Checkout,
    ready: bool,
    problem: str | None,
    active_run_id: str | None,
) -> dict[str, Any]:
    return {
        "name": identity.name,
        "url": url,
        "commit": checkout.commit,
        "local_changes": checkout.local_changes,
        "ready": ready,
        "problem": problem,
        "active_run_id": active_run_id,
    }


def doctor_checks(config: CliConfig, token: str) -> list[DoctorCheck]:
    """Exactly what `os doctor` checks on this machine."""
    return run_doctor(config, token, extra=(*DOCUMENT_CHECKS, secrets_check))


class Presence:
    """This device's row in the hosted list, kept current."""

    def __init__(
        self,
        config: CliConfig,
        identity: DeviceIdentity,
        *,
        repo: Path,
        token: Callable[[], str] | None = None,
        doctor: Callable[[CliConfig, str], list[DoctorCheck]] = doctor_checks,
        tailnet: Callable[[], str] = tailnet_url,
        checkout: Callable[[Path], Checkout] = read_checkout,
        api_factory: Callable[[CliConfig, str], Any] = WorkerApi,
        clock: Callable[[], float] = time.monotonic,
        ensure_api: Callable[[], object] | None = None,
    ) -> None:
        self.config = config
        self.identity = identity
        self.repo = repo
        self.token = token or (lambda: active_session(supabase_auth(config)).access_token)
        self.doctor = doctor
        self.tailnet = tailnet
        self.checkout = checkout
        self.api_factory = api_factory
        self.clock = clock
        self.ensure_api = ensure_api or (
            lambda: service.ensure_running(config.api_url, quiet=True)
        )
        self.ready = False
        self.problem: str | None = "Doctor has not run yet"
        self.checked_at: float | None = None
        self.last: Checkout | None = None

    def check(self) -> None:
        """Run doctor now; the next beats report what it found."""
        self.ensure_api()
        self.ready, self.problem = readiness(self.doctor(self.config, self.token()))
        self.checked_at = self.clock()

    def beat(self, active_run_id: str | None) -> dict[str, Any]:
        """Send one heartbeat; returns the device as the hosted list now has it."""
        if self.checked_at is None or self.clock() - self.checked_at >= DOCTOR_SECONDS:
            self.check()
        ready, problem = self.ready, self.problem
        try:
            url = self.tailnet()
            if url != self.identity.url:
                self.identity = replace(self.identity, url=url)
                save_identity(self.identity)
        except DeviceError as error:
            if self.identity.url is None:
                raise DeviceError(
                    f"{error}. This device has no tailnet address yet; run 'os device install'."
                ) from None
            url, ready, problem = self.identity.url, False, f"Tailscale: {error}"
        self.last = self.checkout(self.repo)
        payload = heartbeat_payload(
            self.identity,
            url=url,
            checkout=self.last,
            ready=ready,
            problem=problem,
            active_run_id=active_run_id,
        )
        self.ensure_api()
        api = self.api_factory(self.config, self.token())
        try:
            row: dict[str, Any] = api.put_device(self.identity.id, payload)
            return row
        finally:
            api.close()
