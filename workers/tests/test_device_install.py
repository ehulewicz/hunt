import base64
import subprocess
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker import cli
from personal_os_worker.device import commands, identity, install
from personal_os_worker.device.identity import DeviceError


def decoded(value: str) -> str:
    return base64.b64decode(value).decode("utf-16-le")


def test_the_logon_task_runs_the_agent_hidden_in_this_distro() -> None:
    script = install.task_script("Ubuntu")
    assert "-AtLogOn" in script and "Register-ScheduledTask" in script
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in script  # never killed after 72 hours
    assert "Start-ScheduledTask" in script
    hidden = script.split("-EncodedCommand ", 1)[1].split("'", 1)[0]
    assert decoded(hidden) == "wsl.exe -d Ubuntu --exec bash -lc 'exec os device serve'"


def test_a_refused_task_says_why() -> None:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        assert argv[0] == install.POWERSHELL
        assert "Register-ScheduledTask" in decoded(argv[-1])
        return subprocess.CompletedProcess(argv, 1, "", "Access is denied.\r\nmore")

    with pytest.raises(DeviceError, match="Access is denied"):
        install.register_logon_task("Ubuntu", run=run)


def test_tailscale_serves_the_loopback_port_over_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install, "tailscale_exe", lambda: "tailscale.exe")
    calls: list[list[str]] = []

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    install.serve_on_tailnet(run=run)
    assert calls == [["tailscale.exe", "serve", "--bg", "--https=443", "http://127.0.0.1:9340"]]
    assert not any("funnel" in part for part in calls[0])


def test_a_serve_left_waiting_says_how_to_see_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install, "tailscale_exe", lambda: "tailscale.exe")

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with pytest.raises(DeviceError, match=r"tailscale\.exe serve --bg --https=443"):
        install.serve_on_tailnet(run=run)


def test_serve_needs_an_installed_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PERSONAL_OS_DEVICE_FILE", str(tmp_path / "device.json"))
    with pytest.raises(DeviceError, match="os device install"):
        commands.run_serve()


def test_status_without_install_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PERSONAL_OS_DEVICE_FILE", str(tmp_path / "device.json"))
    assert commands.run_status() == 1
    assert "os device install" in capsys.readouterr().out


def test_the_cli_routes_device_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(cli.devices, "run_install", lambda: seen.append("install") or 0)
    monkeypatch.setattr(cli.devices, "run_serve", lambda: seen.append("serve") or 0)
    monkeypatch.setattr(cli.devices, "run_status", lambda: seen.append("status") or 0)
    for action in ("install", "serve", "status"):
        assert cli.main(["device", action]) == 0
    assert seen == ["install", "serve", "status"]


def test_remote_runs_take_no_step_and_no_done() -> None:
    with pytest.raises(SystemExit, match="--step"):
        cli.main(["hunt", "apply", "3", "--remote", "--step"])
    with pytest.raises(SystemExit, match="--done takes no other flags"):
        cli.main(["hunt", "apply", "3", "--remote", "--done"])


def test_remote_is_passed_through_to_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(cli.hunt, "run_autofill", lambda ref, **kw: seen.append(kw) or 0)
    monkeypatch.setattr(cli.hunt, "run_autofill_top", lambda **kw: seen.append(kw) or 0)
    cli.main(["hunt", "apply", "3", "--remote"])
    cli.main(["hunt", "apply", "--remote"])
    assert [kw["remote"] for kw in seen] == [True, True]


def test_a_remote_run_with_no_agent_fails_before_it_records_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from personal_os_worker.jobs import commands as hunt

    monkeypatch.setattr(hunt, "socket_path", lambda: tmp_path / "device.sock")
    monkeypatch.setattr(
        hunt, "connected", lambda: (_ for _ in ()).throw(AssertionError("must not connect"))
    )
    with pytest.raises(RuntimeError, match="none is listening"):
        hunt.apply_posting("posting", dry_run=False, no_claude=False, remote=True)


def test_install_is_identity_tailnet_task_then_doctor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from personal_os_worker.core.doctor import DoctorCheck

    monkeypatch.setenv("PERSONAL_OS_DEVICE_FILE", str(tmp_path / "device.json"))
    steps: list[str] = []
    monkeypatch.setattr(commands, "connected", lambda: ("config", "token"))
    monkeypatch.setattr(commands, "tailnet_url", lambda: "https://home.tail1.ts.net")
    monkeypatch.setattr(commands, "windows_machine_name", lambda: "DESKTOP-HOME")
    monkeypatch.setattr(commands, "serve_on_tailnet", lambda: steps.append("serve"))
    monkeypatch.setattr(commands, "wsl_distro", lambda: "Ubuntu")
    monkeypatch.setattr(commands, "register_logon_task", lambda distro: steps.append(distro))
    monkeypatch.setattr(
        commands, "doctor_checks", lambda config, token: [DoctorCheck("Sign-in", True, "ok")]
    )
    assert commands.run_install() == 0
    assert steps == ["serve", "Ubuntu"]
    stored = identity.load_identity()
    assert stored is not None and stored.name == "DESKTOP-HOME"
    assert stored.url == "https://home.tail1.ts.net"
    assert "Ready." in capsys.readouterr().out


def test_a_linked_run_that_fails_tells_the_page_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from personal_os_worker.jobs import commands as hunt

    sent: list[Any] = []
    stopped: list[str] = []

    class Link:
        def log(self, spans: list[Any]) -> None:
            sent.append(spans)

        def end(self, outcome: str) -> None:
            sent.append(outcome)

        def fixes(self, items: list[Any]) -> None:
            pass

        def close(self) -> None:
            sent.append("closed")

    class Api:
        def career(self) -> dict[str, Any]:
            return {"document": {}}

        def create_autofill_run(self, **_: Any) -> dict[str, Any]:
            return {"run_id": "r1", "application_url": "https://jobs.example/1",
                    "company_name": "Acme", "title": "Intern"}

        def stop_autofill_run(self, run_id: str, *, status: str) -> None:
            stopped.append(status)

        def close(self) -> None:
            pass

    class Gone:
        def __init__(self, url: str) -> None:
            pass

        def __enter__(self) -> None:
            raise RuntimeError("Chrome [bold] went away")

        def __exit__(self, *_: object) -> None:
            pass

    (tmp_path / "device.sock").touch()
    monkeypatch.setattr(hunt, "socket_path", lambda: tmp_path / "device.sock")
    monkeypatch.setattr(hunt, "connected", lambda: (type("C", (), {"cdp_url": "x"})(), "t"))
    monkeypatch.setattr(hunt, "ensure_chrome", lambda url: None)
    monkeypatch.setattr(hunt, "WorkerApi", lambda config, token: Api())
    monkeypatch.setattr(
        "personal_os_worker.documents.doctor.latex_toolchain_check",
        lambda: type("Ok", (), {"ok": True})(),
    )
    monkeypatch.setattr("personal_os_worker.documents.resume.ensure_resume", lambda api: None)
    monkeypatch.setattr("personal_os_worker.jobs.browser.VisibleBrowser", Gone)
    monkeypatch.setattr("personal_os_worker.jobs.remote.RunLink.connect", lambda **_: Link())

    with pytest.raises(RuntimeError, match="went away"):
        hunt.apply_posting("posting", dry_run=False, no_claude=False, remote=True)

    assert sent == [[{"text": "Chrome [bold] went away", "style": ["red"]}], "failed", "closed"]
    assert stopped == ["failed"]
    assert "went away" not in capsys.readouterr().out  # the terminal hears it once, from the CLI


def test_serve_without_the_agents_dependencies_says_how_to_get_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "uvicorn", None)  # import uvicorn now raises ImportError
    with pytest.raises(DeviceError, match="uv tool install --reinstall"):
        commands.run_serve()
