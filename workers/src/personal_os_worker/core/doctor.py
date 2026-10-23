from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.config import CliConfig


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    # An advisory check that fails only warns: the machine still works without it.
    advisory: bool = False

    @property
    def label(self) -> str:
        return "OK" if self.ok else "WARN" if self.advisory else "FAIL"

def run_doctor(
    config: CliConfig,
    access_token: str,
    *,
    extra: Sequence[Callable[[], DoctorCheck]] = (),
) -> list[DoctorCheck]:
    """Core checks, plus whatever checks a vertical registers.

    Core knows sign-in, the API and Chrome. It must not know what a resume
    needs to compile, so documents supplies those through `extra`.
    """
    checks = [DoctorCheck("Sign-in", True, "Google session loaded")]
    api = WorkerApi(config, access_token)
    try:
        try:
            api.doctor()
            checks.append(DoctorCheck("Local API", True, f"{config.api_url} accepted the session"))
        except httpx.HTTPError as error:
            checks.append(DoctorCheck("Local API", False, _safe_http_error(error)))
    finally:
        api.close()

    try:
        response = httpx.get(f"{config.cdp_url.rstrip('/')}/json/version", timeout=5)
        response.raise_for_status()
        checks.append(DoctorCheck("Chrome CDP", True, "Visible Chrome is reachable"))
    except httpx.HTTPError as error:
        checks.append(DoctorCheck("Chrome CDP", False, _safe_http_error(error)))

    checks.append(command_check("Claude authentication", "claude", ["auth", "status"]))
    checks.extend(check() for check in extra)
    return checks


def command_check(name: str, executable: str, arguments: list[str]) -> DoctorCheck:
    path = shutil.which(executable)
    if path is None:
        return DoctorCheck(name, False, f"{executable} is not installed or not on PATH")
    result = subprocess.run(
        [path, *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return DoctorCheck(name, False, detail[-1][:300] if detail else "check failed")
    return DoctorCheck(name, True, f"{path} is ready")


def _safe_http_error(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    return error.__class__.__name__
