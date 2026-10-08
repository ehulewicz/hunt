from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import httpx

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.config import CliConfig

LATEX_CHECK = "LaTeX toolchain"


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def latex_toolchain_check() -> DoctorCheck:
    """latexmk runs and TeX can find every file the resume/letter preamble loads.

    A missing package is a machine-level fact, knowable before a browser opens,
    and it fails every cover-letter build. Checking it here turns a mid-
    application surprise into a startup error.
    """
    from personal_os_worker.documents.resume_pdf import preamble_requirements

    binary = _command_check(LATEX_CHECK, "latexmk", ["-v"])
    if not binary.ok:
        return binary
    kpsewhich = shutil.which("kpsewhich")
    if kpsewhich is None:
        return DoctorCheck(LATEX_CHECK, False, "kpsewhich is not installed or not on PATH")
    required = preamble_requirements()
    missing = [name for name in required if not _tex_file_found(kpsewhich, name)]
    if missing:
        hint = (
            "install texlive-fonts-extra"
            if any(name.startswith("newtx") for name in missing)
            else "install the TeX Live packages that provide them"
        )
        return DoctorCheck(LATEX_CHECK, False, f"TeX cannot find {', '.join(missing)}; {hint}")
    return DoctorCheck(LATEX_CHECK, True, f"latexmk and {len(required)} preamble file(s) are ready")


def _tex_file_found(kpsewhich: str, name: str) -> bool:
    result = subprocess.run(
        [kpsewhich, name], check=False, capture_output=True, text=True, timeout=15
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def run_doctor(config: CliConfig, access_token: str) -> list[DoctorCheck]:
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

    checks.append(_command_check("Claude authentication", "claude", ["auth", "status"]))
    checks.append(latex_toolchain_check())
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
