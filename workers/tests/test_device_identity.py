import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.device import identity
from personal_os_worker.device.identity import DeviceError, DeviceIdentity


def answering(stdout: str, code: int = 0) -> Any:
    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, code, stdout, "")

    return run


@pytest.fixture(autouse=True)
def windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(identity.Path, "exists", lambda _: True)
    monkeypatch.setenv("PERSONAL_OS_DEVICE_FILE", str(tmp_path / "device.json"))


def test_the_address_is_the_machines_magicdns_name_over_https() -> None:
    status = {"BackendState": "Running", "Self": {"DNSName": "Desktop-Home.tail1234.ts.net."}}
    assert identity.tailnet_url(answering(json.dumps(status))) == (
        "https://desktop-home.tail1234.ts.net"
    )


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        (json.dumps({"BackendState": "Stopped", "Self": {}}), "Stopped"),
        ("", "running"),
        (json.dumps({"BackendState": "Running", "Self": {"DNSName": ""}}), "MagicDNS"),
    ],
)
def test_tailscale_that_cannot_serve_says_why(stdout: str, message: str) -> None:
    with pytest.raises(DeviceError, match=message):
        identity.tailnet_url(answering(stdout))


def test_the_name_is_the_windows_computer_name() -> None:
    assert identity.windows_machine_name(answering("DESKTOP-HOME\r\n")) == "DESKTOP-HOME"
    with pytest.raises(DeviceError):
        identity.windows_machine_name(answering("%COMPUTERNAME%\r\n"))


def test_the_id_is_made_once_and_kept() -> None:
    first = identity.ensure_identity(name="DESKTOP-HOME", url="https://a.tail1.ts.net")
    second = identity.ensure_identity(name="DESKTOP-RENAMED", url=None)
    assert second.id == first.id
    assert second.name == "DESKTOP-RENAMED"
    assert second.url == "https://a.tail1.ts.net"
    assert identity.load_identity() == second


def test_extra_page_origins_survive_a_round_trip() -> None:
    made = DeviceIdentity(
        id="33333333-3333-4333-8333-333333333333",
        name="LAPTOP",
        page_origins=("https://hunt.example.com", "http://localhost:3000"),
    )
    identity.save_identity(made)
    assert identity.load_identity() == made


def test_a_damaged_file_reads_as_no_identity() -> None:
    identity.device_path().write_text("{not json")
    assert identity.load_identity() is None
