from __future__ import annotations

import pytest

from personal_os_worker import service


def test_already_running_api_is_not_started_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "is_running", lambda *a, **k: True)

    def explode(*_: object, **__: object) -> None:
        raise AssertionError("must not spawn a second API")

    monkeypatch.setattr(service.subprocess, "Popen", explode)
    assert service.ensure_running("http://127.0.0.1:8000") is False


def test_missing_executable_explains_how_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(service.shutil, "which", lambda _: None)
    with pytest.raises(service.ApiUnavailable, match="uv tool install"):
        service.ensure_running("http://127.0.0.1:8000")


def test_start_waits_for_health_then_reports_it_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "log_path", lambda: tmp_path / "api.log")
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/personal-os-api")
    spawned: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: object) -> object:
        spawned.append(command)
        # Detaching matters: the API must outlive this CLI invocation.
        assert kwargs["start_new_session"] is True
        return object()

    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)
    health = iter([False, False, True])
    monkeypatch.setattr(service, "is_running", lambda *a, **k: next(health))
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    assert service.ensure_running("http://127.0.0.1:8000", quiet=True) is True
    assert spawned == [["/usr/bin/personal-os-api", "--port", "8000"]]


def test_start_gives_up_and_points_at_the_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    log = tmp_path / "api.log"
    monkeypatch.setattr(service, "log_path", lambda: log)
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/personal-os-api")
    monkeypatch.setattr(service.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(service, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    monkeypatch.setattr(service, "STARTUP_TIMEOUT_SECONDS", 0.0)
    with pytest.raises(service.ApiUnavailable, match=str(log)):
        service.ensure_running("http://127.0.0.1:8000", quiet=True)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8000", "8000"),
        ("http://127.0.0.1:9999/", "9999"),
        ("http://localhost:8123", "8123"),
    ],
)
def test_port_is_taken_from_the_configured_url(url: str, expected: str) -> None:
    assert service._port_of(url) == expected


def test_running_chrome_is_not_relaunched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "chrome_is_running", lambda *a, **k: True)

    def explode(*_: object, **__: object) -> None:
        raise AssertionError("must not spawn a second Chrome")

    monkeypatch.setattr(service.subprocess, "Popen", explode)
    assert service.ensure_chrome("http://127.0.0.1:9333") is False


def test_chrome_launches_with_a_dedicated_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.Path, "exists", lambda _: True)
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        service.subprocess, "Popen", lambda cmd, **k: spawned.append(cmd) or object()
    )
    health = iter([False, True])
    monkeypatch.setattr(service, "chrome_is_running", lambda *a, **k: next(health))
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    assert service.ensure_chrome("http://127.0.0.1:9333", quiet=True) is True
    command = spawned[0]
    assert "--remote-debugging-port=9333" in command
    # Chrome 136+ serves 404 on /json/* for the default profile, so this is required.
    assert any(arg.startswith("--user-data-dir=") for arg in command)


def test_missing_chrome_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "chrome_is_running", lambda *a, **k: False)
    monkeypatch.setattr(service.Path, "exists", lambda _: False)
    with pytest.raises(service.ChromeUnavailable, match="not found"):
        service.ensure_chrome("http://127.0.0.1:9333")
