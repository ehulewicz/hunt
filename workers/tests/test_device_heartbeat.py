from pathlib import Path
from typing import Any, ClassVar

import pytest

from personal_os_worker.core.config import CliConfig
from personal_os_worker.core.doctor import DoctorCheck
from personal_os_worker.device import heartbeat, identity
from personal_os_worker.device.heartbeat import Presence, heartbeat_payload, readiness
from personal_os_worker.device.identity import DeviceError, DeviceIdentity
from personal_os_worker.device.update import Checkout

CONFIG = CliConfig("http://127.0.0.1:8000", "https://example.supabase.co", "key")
DEVICE = DeviceIdentity(
    id="33333333-3333-4333-8333-333333333333",
    name="DESKTOP-HOME",
    url="https://desktop-home.tail1234.ts.net",
)
CLEAN = Checkout(commit="c" * 40, branch="main", dirty=False, ahead=0, behind=0)


def checks(*failing: str) -> list[DoctorCheck]:
    names = ["Sign-in", "Local API", "Chrome CDP", "Claude authentication", "LaTeX"]
    return [DoctorCheck(name, name not in failing, f"{name} detail") for name in names]


def test_ready_means_every_doctor_check_passed() -> None:
    assert readiness(checks()) == (True, None)
    assert readiness(checks("Claude authentication", "LaTeX")) == (
        False,
        "Claude authentication: Claude authentication detail",
    )


def test_a_closed_chrome_does_not_make_the_device_unready() -> None:
    """The agent starts Chrome when a page connects or a run starts."""
    assert readiness(checks("Chrome CDP")) == (True, None)


def test_the_payload_is_what_the_hosted_row_holds() -> None:
    payload = heartbeat_payload(
        DEVICE,
        url="https://desktop-home.tail1234.ts.net",
        checkout=Checkout(commit="d" * 40, branch="main", dirty=True, ahead=0, behind=0),
        ready=False,
        problem="LaTeX: missing",
        active_run_id="44444444-4444-4444-8444-444444444444",
    )
    assert payload == {
        "name": "DESKTOP-HOME",
        "url": "https://desktop-home.tail1234.ts.net",
        "commit": "d" * 40,
        "local_changes": True,
        "ready": False,
        "problem": "LaTeX: missing",
        "active_run_id": "44444444-4444-4444-8444-444444444444",
    }
    assert "id" not in payload and "user_id" not in payload


class FakeApi:
    sent: ClassVar[list[tuple[str, dict[str, Any]]]] = []

    def __init__(self, config: CliConfig, token: str) -> None:
        self.token = token

    def put_device(self, device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        FakeApi.sent.append((device_id, payload))
        return {"id": device_id, **payload, "online": True}

    def close(self) -> None:
        pass


@pytest.fixture
def presence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Presence:
    monkeypatch.setenv("PERSONAL_OS_DEVICE_FILE", str(tmp_path / "device.json"))
    identity.save_identity(DEVICE)
    FakeApi.sent = []
    now = [1000.0]
    doctor_runs: list[int] = []

    def doctor(config: CliConfig, token: str) -> list[DoctorCheck]:
        doctor_runs.append(1)
        return checks()

    made = Presence(
        CONFIG,
        DEVICE,
        repo=tmp_path,
        token=lambda: "access",
        doctor=doctor,
        tailnet=lambda: "https://desktop-home.tail1234.ts.net",
        checkout=lambda repo: CLEAN,
        api_factory=FakeApi,
        clock=lambda: now[0],
        ensure_api=lambda: None,
    )
    made.doctor_runs = doctor_runs  # type: ignore[attr-defined]
    made.now = now  # type: ignore[attr-defined]
    return made


def test_a_beat_reuses_the_doctor_until_it_goes_stale(presence: Presence) -> None:
    presence.beat(None)
    presence.now[0] += heartbeat.HEARTBEAT_SECONDS  # type: ignore[attr-defined]
    presence.beat("44444444-4444-4444-8444-444444444444")
    assert len(presence.doctor_runs) == 1  # type: ignore[attr-defined]
    presence.now[0] += heartbeat.DOCTOR_SECONDS  # type: ignore[attr-defined]
    presence.beat(None)
    assert len(presence.doctor_runs) == 2  # type: ignore[attr-defined]
    assert [payload["active_run_id"] for _, payload in FakeApi.sent] == [
        None, "44444444-4444-4444-8444-444444444444", None,
    ]
    assert {device_id for device_id, _ in FakeApi.sent} == {DEVICE.id}


def test_without_tailscale_the_last_address_is_kept_and_the_device_is_not_ready(
    presence: Presence,
) -> None:
    def down() -> str:
        raise DeviceError("Tailscale is Stopped")

    presence.tailnet = down
    presence.beat(None)
    _, payload = FakeApi.sent[-1]
    assert payload["url"] == DEVICE.url
    assert payload["ready"] is False
    assert payload["problem"] == "Tailscale: Tailscale is Stopped"


def test_a_device_with_no_address_yet_cannot_list_itself(presence: Presence) -> None:
    def down() -> str:
        raise DeviceError("Tailscale is Stopped")

    presence.tailnet = down
    presence.identity = DeviceIdentity(id=DEVICE.id, name=DEVICE.name, url=None)
    with pytest.raises(DeviceError, match="os device install"):
        presence.beat(None)
    assert FakeApi.sent == []


def test_a_new_address_is_remembered(presence: Presence, tmp_path: Path) -> None:
    presence.tailnet = lambda: "https://renamed.tail1234.ts.net"
    presence.beat(None)
    assert identity.load_identity() is not None
    assert identity.load_identity().url == "https://renamed.tail1234.ts.net"  # type: ignore[union-attr]
