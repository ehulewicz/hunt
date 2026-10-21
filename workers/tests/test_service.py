from __future__ import annotations

import pytest

from personal_os_worker.core import service


def test_already_running_api_is_not_started_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    # Isolated from the machine's real service record, which may name older source.
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "is_running", lambda *a, **k: True)

    def explode(*_: object, **__: object) -> None:
        raise AssertionError("must not spawn a second API")

    monkeypatch.setattr(service.subprocess, "Popen", explode)
    assert service.ensure_running("http://127.0.0.1:8000") is False


def test_api_started_from_older_backend_source_is_restarted(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "log_path", lambda: tmp_path / "api.log")
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/personal-os-backend")
    monkeypatch.setattr(service, "backend_source_fingerprint", lambda: "after-pull")
    service.record_service("api", 4242, "8000", source="before-pull")
    events: list[str] = []
    monkeypatch.setattr(
        service, "stop_api", lambda url, **_: events.append("stop") or True
    )

    def fake_popen(command: list[str], **kwargs: object) -> object:
        events.append("start")
        return type("P", (), {"pid": 4343})()

    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(service, "is_running", lambda *a, **k: True)

    assert service.ensure_running("http://127.0.0.1:8000", quiet=True) is True
    assert events == ["stop", "start"]
    record = service.read_service("api")
    assert record is not None and record.source == "after-pull" and record.pid == 4343


def test_api_started_from_the_current_source_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "backend_source_fingerprint", lambda: "same")
    service.record_service("api", 4242, "8000", source="same")
    monkeypatch.setattr(service, "is_running", lambda *a, **k: True)
    monkeypatch.setattr(service, "stop_api", lambda *a, **k: pytest.fail("must not restart"))

    assert service.ensure_running("http://127.0.0.1:8000") is False


def test_missing_executable_explains_how_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(service.shutil, "which", lambda _: None)
    with pytest.raises(service.ApiUnavailable, match="uv tool install"):
        service.ensure_running("http://127.0.0.1:8000")


def test_start_waits_for_health_then_reports_it_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "log_path", lambda: tmp_path / "api.log")
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/personal-os-backend")
    spawned: list[list[str]] = []

    def fake_popen(command: list[str], **kwargs: object) -> object:
        spawned.append(command)
        # Detaching matters: the API must outlive this CLI invocation.
        assert kwargs["start_new_session"] is True
        return type("P", (), {"pid": 4242})()

    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)
    health = iter([False, False, True])
    monkeypatch.setattr(service, "is_running", lambda *a, **k: next(health))
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    assert service.ensure_running("http://127.0.0.1:8000", quiet=True) is True
    assert spawned == [["/usr/bin/personal-os-backend", "--port", "8000"]]


def test_start_gives_up_and_points_at_the_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    log = tmp_path / "api.log"
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "log_path", lambda: log)
    monkeypatch.setattr(service.shutil, "which", lambda _: "/usr/bin/personal-os-backend")
    monkeypatch.setattr(
        service.subprocess, "Popen", lambda *a, **k: type("P", (), {"pid": 4242})()
    )
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
        ("http://127.0.0.1", "8000"),
    ],
)
def test_port_is_taken_from_the_configured_url(url: str, expected: str) -> None:
    assert service._port_of(url) == expected


def test_hosted_api_is_checked_but_never_started_or_restarted(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    # The stale record a failed local start used to leave behind.
    service.record_service("api", 4242, "//api.example.run.app", source="before-pull")
    monkeypatch.setattr(service, "is_running", lambda *a, **k: True)
    monkeypatch.setattr(service, "stop_api", lambda *a, **k: pytest.fail("must not stop"))
    monkeypatch.setattr(
        service.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn")
    )

    assert service.ensure_running("https://api.example.run.app", quiet=True) is False


def test_unreachable_hosted_api_is_reported_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "is_running", lambda *a, **k: False)
    monkeypatch.setattr(
        service.subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn")
    )
    with pytest.raises(service.ApiUnavailable, match="hosted API"):
        service.ensure_running("https://api.example.run.app")


def test_stopping_a_hosted_api_only_forgets_the_stale_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    service.record_service("api", 4242, "//api.example.run.app")
    monkeypatch.setattr(service.os, "kill", lambda *a: pytest.fail("must not signal"))

    assert service.stop_api("https://api.example.run.app", force=True) is False
    assert service.read_service("api") is None


def test_running_chrome_is_not_relaunched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "chrome_is_running", lambda *a, **k: True)

    def explode(*_: object, **__: object) -> None:
        raise AssertionError("must not spawn a second Chrome")

    monkeypatch.setattr(service.subprocess, "Popen", explode)
    assert service.ensure_chrome("http://127.0.0.1:9333") is False


def test_a_busy_chrome_is_waited_for_not_relaunched(monkeypatch: pytest.MonkeyPatch) -> None:
    # A Chrome slow to answer still holds its port. Relaunching it only opens another
    # window in the running browser, which slows it further: 435 windows, once.
    monkeypatch.setattr(service, "_port_refuses", lambda *a, **k: False)
    health = iter([False, False, True])
    monkeypatch.setattr(service, "chrome_is_running", lambda *a, **k: next(health))
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    def explode(*_: object, **__: object) -> None:
        raise AssertionError("must not launch Chrome over a running one")

    monkeypatch.setattr(service.subprocess, "Popen", explode)
    assert service.ensure_chrome("http://127.0.0.1:9333", quiet=True) is False


def test_a_chrome_that_never_answers_is_reported_not_relaunched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "_port_refuses", lambda *a, **k: False)
    monkeypatch.setattr(service, "chrome_is_running", lambda *a, **k: False)
    monkeypatch.setattr(service, "CHROME_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(service.subprocess, "Popen", lambda *a, **k: pytest.fail("launched"))
    with pytest.raises(service.ChromeUnavailable, match="not answering"):
        service.ensure_chrome("http://127.0.0.1:9333", quiet=True)


def test_chrome_launches_with_a_dedicated_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_port_refuses", lambda *a, **k: True)
    monkeypatch.setattr(service.Path, "exists", lambda _: True)
    monkeypatch.setattr(service, "chrome_profile", lambda: r"C:\Users\someone\personal-os-chrome")
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
    assert r"--user-data-dir=C:\Users\someone\personal-os-chrome" in command
    # The page streams this window; a covered, minimized or locked one must keep painting.
    assert "--disable-backgrounding-occluded-windows" in command
    assert "--disable-renderer-backgrounding" in command
    assert "--disable-background-timer-throttling" in command


def test_the_profile_lives_in_the_windows_home_of_whoever_runs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service.windows_home.cache_clear()
    monkeypatch.delenv("PERSONAL_OS_WINDOWS_HOME", raising=False)
    monkeypatch.setattr(service.Path, "exists", lambda _: True)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> object:
        calls.append(cmd)
        return type("R", (), {"stdout": "C:\\Users\\Parent\r\n", "returncode": 0})()

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    try:
        assert service.chrome_profile() == r"C:\Users\Parent\personal-os-chrome"
        assert service.chrome_profile() == r"C:\Users\Parent\personal-os-chrome"
        assert len(calls) == 1  # resolved once
        assert "%USERPROFILE%" in calls[0][-1]
    finally:
        service.windows_home.cache_clear()


def test_the_windows_home_can_be_named_outright(monkeypatch: pytest.MonkeyPatch) -> None:
    service.windows_home.cache_clear()
    monkeypatch.setenv("PERSONAL_OS_WINDOWS_HOME", r"D:\Home\me")
    try:
        assert service.chrome_profile() == r"D:\Home\me\personal-os-chrome"
    finally:
        service.windows_home.cache_clear()


def test_without_windows_there_is_no_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    service.windows_home.cache_clear()
    monkeypatch.delenv("PERSONAL_OS_WINDOWS_HOME", raising=False)
    monkeypatch.setattr(service.Path, "exists", lambda _: False)
    try:
        with pytest.raises(service.ChromeUnavailable, match="Windows"):
            service.chrome_profile()
    finally:
        service.windows_home.cache_clear()


def test_missing_chrome_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_port_refuses", lambda *a, **k: True)
    monkeypatch.setattr(service, "chrome_is_running", lambda *a, **k: False)
    monkeypatch.setattr(service.Path, "exists", lambda _: False)
    with pytest.raises(service.ChromeUnavailable, match="not found"):
        service.ensure_chrome("http://127.0.0.1:9333")


def test_open_external_url_uses_native_windows_chrome_from_wsl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.Path, "exists", lambda _: True)
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        service.subprocess, "Popen", lambda cmd, **kwargs: spawned.append(cmd) or object()
    )

    service.open_external_url("https://jobs.example/apply/1")

    assert spawned == [[service.CHROME_EXE, "https://jobs.example/apply/1"]]


def test_open_external_url_rejects_non_web_schemes() -> None:
    with pytest.raises(RuntimeError, match="Only http"):
        service.open_external_url("file:///tmp/not-a-listing")


def test_registry_round_trips_and_forgets(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    service.record_service("api", 4242, "8000")
    record = service.read_service("api")
    assert record is not None
    assert (record.name, record.pid, record.port) == ("api", 4242, "8000")
    service.forget_service("api")
    assert service.read_service("api") is None


def test_unreadable_registry_reads_as_absent(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    path = service.registry_path("api")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert service.read_service("api") is None


def test_stop_api_signals_the_recorded_pid(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    service.record_service("api", 4242, "8000")
    signalled: list[int] = []
    monkeypatch.setattr(service.os, "kill", lambda pid, sig: signalled.append(pid))
    monkeypatch.setattr(service, "is_running", lambda *a, **k: False)
    assert service.stop_api("http://127.0.0.1:8000") is True
    assert signalled == [4242]
    # The record must not survive, or a later stop would signal a dead pid.
    assert service.read_service("api") is None


def test_stop_api_without_a_record_does_nothing_unless_forced(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "_pid_on_port", lambda _: None)
    assert service.stop_api("http://127.0.0.1:8000") is False

    monkeypatch.setattr(service, "_pid_on_port", lambda _: 777)
    monkeypatch.setattr(service.os, "kill", lambda *a: None)
    monkeypatch.setattr(service, "is_running", lambda *a, **k: False)
    assert service.stop_api("http://127.0.0.1:8000", force=True) is True


def test_stop_chrome_kills_windows_pids_not_linux_ones(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """Chrome is a Windows process; a Linux signal would leave it running."""
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "windows_chrome_pids", lambda: [1234, 5678])
    monkeypatch.setattr(service.Path, "exists", lambda _: True)
    monkeypatch.setattr(service, "chrome_is_running", lambda *a, **k: False)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        service.subprocess,
        "run",
        lambda cmd, **k: calls.append(cmd) or type("R", (), {"stdout": "", "returncode": 0})(),
    )
    assert service.stop_chrome("http://127.0.0.1:9333") is True
    killed = [c[c.index("/PID") + 1] for c in calls if "/PID" in c]
    assert killed == ["1234", "5678"]
    # /IM chrome.exe would close the user's own browser windows too.
    assert not any("/IM" in c for c in calls)


def test_stop_chrome_with_nothing_running_is_a_no_op(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(service, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "windows_chrome_pids", lambda: [])
    assert service.stop_chrome("http://127.0.0.1:9333") is False
