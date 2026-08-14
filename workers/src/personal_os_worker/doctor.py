from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import httpx

from personal_os_worker.api import WorkerApi
from personal_os_worker.config import WorkerConfig


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def run_doctor(config: WorkerConfig) -> list[DoctorCheck]:
    checks = [DoctorCheck("Pairing", True, "Worker configuration loaded")]
    api = WorkerApi(config)
    try:
        try:
            api.doctor()
            checks.append(DoctorCheck("Hosted API", True, "Worker credential accepted"))
        except httpx.HTTPError as error:
            checks.append(DoctorCheck("Hosted API", False, _safe_http_error(error)))
    finally:
        api.close()

    try:
        response = httpx.get(f"{config.cdp_url.rstrip('/')}/json/version", timeout=5)
        response.raise_for_status()
        checks.append(DoctorCheck("Chrome CDP", True, "Visible Chrome is reachable"))
    except httpx.HTTPError as error:
        checks.append(DoctorCheck("Chrome CDP", False, _safe_http_error(error)))

    checks.append(_command_check("Claude authentication", "claude", ["auth", "status"]))
    checks.append(_command_check("latexmk", "latexmk", ["-v"]))
    checks.append(_command_check("pdfinfo", "pdfinfo", ["-v"]))
    return checks


def _command_check(name: str, executable: str, arguments: list[str]) -> DoctorCheck:
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
