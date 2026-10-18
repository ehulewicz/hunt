from __future__ import annotations

import subprocess
from collections.abc import Iterator
from typing import Any

import pytest

from personal_os_worker.core import secrets


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    secrets.clear_cache()
    yield
    secrets.clear_cache()


def fake_gcloud(
    monkeypatch: pytest.MonkeyPatch, outputs: dict[str, tuple[int, str, str]]
) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(secrets.shutil, "which", lambda _: "/usr/bin/gcloud")

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        name = next(part.split("=", 1)[1] for part in command if part.startswith("--secret="))
        code, stdout, stderr = outputs[name]
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    monkeypatch.setattr(secrets.subprocess, "run", run)
    return calls


def test_reads_through_gcloud_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_gcloud(monkeypatch, {"jobs-ats-password": (0, "hunter2\n", "")})
    assert secrets.read_secret(secrets.ATS_PASSWORD) == "hunter2"
    assert secrets.read_secret(secrets.ATS_PASSWORD) == "hunter2"
    assert calls == [
        [
            "/usr/bin/gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            "--secret=jobs-ats-password",
            "--project=example-gcp-project",
        ]
    ]


def test_missing_gcloud_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets.shutil, "which", lambda _: None)
    with pytest.raises(secrets.SecretUnavailable, match="gcloud"):
        secrets.read_secret(secrets.ATS_PASSWORD)


def test_gcloud_error_message_has_no_value(monkeypatch: pytest.MonkeyPatch) -> None:
    error = "ERROR: NOT_FOUND: Secret [jobs-ats-password] not found"
    fake_gcloud(monkeypatch, {"jobs-ats-password": (1, "", f"some preamble\n{error}\n")})
    with pytest.raises(secrets.SecretUnavailable) as raised:
        secrets.read_secret(secrets.ATS_PASSWORD)
    assert str(raised.value).endswith(error)


def test_loaded_values_lists_read_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gcloud(
        monkeypatch,
        {"jobs-ats-password": (0, "a", ""), "jobs-gmail-app-password": (0, "b", "")},
    )
    secrets.read_secret(secrets.ATS_PASSWORD)
    secrets.read_secret(secrets.GMAIL_APP_PASSWORD)
    assert set(secrets.loaded_values()) == {"a", "b"}


def test_doctor_check_reports_without_values(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gcloud(
        monkeypatch,
        {
            "jobs-ats-password": (0, "visible-if-leaked", ""),
            "jobs-gmail-app-password": (1, "", "ERROR: NOT_FOUND"),
        },
    )
    check = secrets.secrets_check()
    assert check.name == "Account secrets"
    assert check.ok is False
    assert "jobs-gmail-app-password" in check.detail
    assert "visible-if-leaked" not in check.detail


def test_doctor_check_passes_when_both_read(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gcloud(
        monkeypatch,
        {"jobs-ats-password": (0, "a", ""), "jobs-gmail-app-password": (0, "b", "")},
    )
    check = secrets.secrets_check()
    assert check.ok is True
    assert "a" not in check.detail.split()
