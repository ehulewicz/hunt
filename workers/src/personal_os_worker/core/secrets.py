"""Credentials the worker needs at run time, read from GCP Secret Manager.

Secret Manager is the one place they live, so every machine reads the same
values after a single `gcloud auth login`. Values stay in this process's memory:
nothing here writes them to disk, and `loaded_values` lets evidence capture
strip any that leak into a page it saves.
"""

from __future__ import annotations

import shutil
import subprocess

from personal_os_worker.core.doctor import DoctorCheck

PROJECT = "example-gcp-project"
ATS_PASSWORD = "jobs-ats-password"
GMAIL_APP_PASSWORD = "jobs-gmail-app-password"

_cache: dict[str, str] = {}


class SecretUnavailable(RuntimeError):
    """Raised when a secret cannot be read; the message never holds a value."""


def read_secret(name: str) -> str:
    if name in _cache:
        return _cache[name]
    gcloud = shutil.which("gcloud")
    if gcloud is None:
        raise SecretUnavailable("gcloud is not installed or not on PATH")
    try:
        result = subprocess.run(
            [gcloud, "secrets", "versions", "access", "latest", f"--secret={name}",
             f"--project={PROJECT}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        # gcloud waiting on a re-login prompt hangs rather than failing.
        raise SecretUnavailable(f"{name}: gcloud did not answer in 30s") from error
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        detail = lines[-1][:300] if lines else f"gcloud exited {result.returncode}"
        raise SecretUnavailable(f"{name}: {detail}")
    value = result.stdout.removesuffix("\n")
    if not value:
        raise SecretUnavailable(f"{name}: the secret is empty")
    _cache[name] = value
    return value


def loaded_values() -> tuple[str, ...]:
    return tuple(value for value in _cache.values() if value)


def clear_cache() -> None:
    _cache.clear()


def secrets_check() -> DoctorCheck:
    missing: list[str] = []
    for name in (ATS_PASSWORD, GMAIL_APP_PASSWORD):
        try:
            read_secret(name)
        except SecretUnavailable as error:
            missing.append(str(error))
    if missing:
        return DoctorCheck("Account secrets", False, "; ".join(missing))
    return DoctorCheck("Account secrets", True, f"both readable from {PROJECT}")
