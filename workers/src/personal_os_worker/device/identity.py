"""Who this device is: an id generated once, its Windows name, its tailnet address.

The id is the device's own and survives reinstalls, so the hosted list keeps one
row per machine. The address comes from Windows Tailscale, which publishes the
agent at `https://<machine>.<tailnet>.ts.net` through `tailscale serve`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# The agent listens here, on loopback only; `tailscale serve` is its only way in.
AGENT_PORT = 9340
# The Vercel workspace is the only page that may drive a device.
PAGE_ORIGINS = ("https://hunt.example.com",)
TAILSCALE_EXE = "/mnt/c/Program Files/Tailscale/tailscale.exe"
CMD = "/mnt/c/Windows/System32/cmd.exe"

Run = Callable[..., subprocess.CompletedProcess[str]]


class DeviceError(RuntimeError):
    """Raised when this machine cannot act as a device."""


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    id: str
    name: str
    url: str | None = None  # the last tailnet address Tailscale reported
    page_origins: tuple[str, ...] = field(default=PAGE_ORIGINS)


def device_path() -> Path:
    configured = os.environ.get("PERSONAL_OS_DEVICE_FILE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "personal-os" / "device.json"


def load_identity() -> DeviceIdentity | None:
    try:
        data = json.loads(device_path().read_text())
        origins = data.get("page_origins")
        return DeviceIdentity(
            id=str(uuid.UUID(str(data["id"]))),
            name=str(data["name"]),
            url=str(data["url"]) if data.get("url") else None,
            page_origins=tuple(str(item) for item in origins) if origins else PAGE_ORIGINS,
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def save_identity(identity: DeviceIdentity) -> Path:
    path = device_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"id": identity.id, "name": identity.name, "url": identity.url}
    if identity.page_origins != PAGE_ORIGINS:
        data["page_origins"] = list(identity.page_origins)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)
    return path


def ensure_identity(*, name: str, url: str | None) -> DeviceIdentity:
    """The stored identity with this name and address, created on first install."""
    existing = load_identity()
    identity = (
        replace(existing, name=name, url=url or existing.url)
        if existing
        else DeviceIdentity(id=str(uuid.uuid4()), name=name, url=url)
    )
    save_identity(identity)
    return identity


def windows_machine_name(run: Run = subprocess.run) -> str:
    """The Windows computer name, which is what the device list calls it."""
    if not Path(CMD).exists():
        raise DeviceError("This is not WSL on Windows; a device is a Windows machine")
    result = run(
        [CMD, "/d", "/c", "echo %COMPUTERNAME%"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        cwd="/mnt/c",
    )
    name = result.stdout.strip()
    if result.returncode != 0 or not name or "%" in name:
        raise DeviceError("Could not read the Windows computer name through cmd.exe")
    return name


def tailscale_exe() -> str | None:
    if Path(TAILSCALE_EXE).exists():
        return TAILSCALE_EXE
    return shutil.which("tailscale.exe")


def tailnet_url(run: Run = subprocess.run) -> str:
    """`https://<machine>.<tailnet>.ts.net`, from Windows Tailscale's own status."""
    executable = tailscale_exe()
    if executable is None:
        raise DeviceError("Tailscale for Windows is not installed")
    result = run(
        [executable, "status", "--json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise DeviceError("Tailscale did not report its status; is it running?") from None
    if status.get("BackendState") != "Running":
        raise DeviceError(f"Tailscale is {status.get('BackendState') or 'not running'}")
    name = str((status.get("Self") or {}).get("DNSName") or "").rstrip(".").casefold()
    if not name.endswith(".ts.net"):
        raise DeviceError("Tailscale has no MagicDNS name for this machine; turn MagicDNS on")
    return f"https://{name}"
