"""Keep the local API running so the CLI is the only command to remember.

The API is a local dependency of the CLI, not something to manage by hand, so
commands that need it start it on demand and leave it running for the next one.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import httpx

API_EXECUTABLE = "personal-os-api"
STARTUP_TIMEOUT_SECONDS = 45.0
_POLL_SECONDS = 0.25
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
TASKKILL = "/mnt/c/Windows/System32/taskkill.exe"

# Jobs drives the Windows Chrome build rather than the WSLg one: it renders
# properly and keeps the real profile, including the Google session. Reaching its
# CDP port from WSL needs networkingMode=mirrored in .wslconfig, because Chrome
# binds remote debugging to loopback only.
CHROME_EXE = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
CHROME_PROFILE = r"C:\Users\eahtr\personal-os-chrome"
CHROME_TIMEOUT_SECONDS = 30.0


class ApiUnavailable(RuntimeError):
    """Raised when the local API is neither running nor startable."""


class ChromeUnavailable(RuntimeError):
    """Raised when the automation browser is neither running nor startable."""


def open_external_url(url: str) -> None:
    """Open an HTTP URL in the host machine's ordinary browser."""
    if not url.startswith(("https://", "http://")):
        raise RuntimeError("Only http:// and https:// listing links can be opened")

    command: list[str] | None = None
    if Path(CHROME_EXE).exists():
        # Under WSL, launch native Windows Chrome without the automation profile.
        command = [CHROME_EXE, url]
    elif sys.platform == "darwin":
        command = ["open", url]
    elif sys.platform.startswith("linux"):
        opener = shutil.which("xdg-open")
        if opener is not None:
            command = [opener, url]

    if command is None:
        if not webbrowser.open(url, new=2):
            raise RuntimeError(f"Could not open the browser. Link: {url}")
        return
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        raise RuntimeError(f"Could not open the browser. Link: {url}") from error


def state_dir() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".local" / "state"
    return base / "personal-os"


def log_path() -> Path:
    return state_dir() / "api.log"


def registry_path(name: str) -> Path:
    return state_dir() / "services" / f"{name}.json"


@dataclass(frozen=True, slots=True)
class ServiceRecord:
    name: str
    pid: int
    port: str
    started_at: float


def record_service(name: str, pid: int, port: str) -> None:
    """Track a service we started, so stopping it never has to guess."""
    path = registry_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"name": name, "pid": pid, "port": port, "started_at": time.time()}, indent=2)
        + "\n"
    )


def read_service(name: str) -> ServiceRecord | None:
    try:
        data = json.loads(registry_path(name).read_text())
        return ServiceRecord(
            name=str(data["name"]),
            pid=int(data["pid"]),
            port=str(data["port"]),
            started_at=float(data["started_at"]),
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def forget_service(name: str) -> None:
    registry_path(name).unlink(missing_ok=True)


def is_running(api_url: str, *, timeout: float = 2.0) -> bool:
    try:
        response = httpx.get(f"{api_url.rstrip('/')}/health", timeout=timeout)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _port_of(api_url: str) -> str:
    _, _, tail = api_url.rstrip("/").rpartition(":")
    return tail or "8000"


def ensure_running(api_url: str, *, quiet: bool = False) -> bool:
    """Return True when the API had to be started, False when it was already up."""
    if is_running(api_url):
        return False

    executable = shutil.which(API_EXECUTABLE)
    if executable is None:
        raise ApiUnavailable(
            f"The local API is not running and {API_EXECUTABLE} is not on PATH. "
            "Install it with: uv tool install --editable ./backend"
        )

    destination = log_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    port = _port_of(api_url)
    handle = destination.open("a")
    try:
        process = subprocess.Popen(
            [executable, "--port", port],
            stdout=handle,
            stderr=handle,
            stdin=subprocess.DEVNULL,
            # Detach so the API outlives this CLI invocation and is reused by the next.
            start_new_session=True,
        )
    finally:
        handle.close()
    record_service("api", process.pid, port)

    if not quiet:
        print(f"Starting the local API ... (log: {destination})")
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if is_running(api_url, timeout=1.0):
            return True
        time.sleep(_POLL_SECONDS)

    raise ApiUnavailable(
        f"The local API did not become healthy within {STARTUP_TIMEOUT_SECONDS:.0f}s. "
        f"Check {destination} for the reason."
    )


def chrome_is_running(cdp_url: str, *, timeout: float = 2.0) -> bool:
    try:
        response = httpx.get(f"{cdp_url.rstrip('/')}/json/version", timeout=timeout)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def ensure_chrome(cdp_url: str, *, quiet: bool = False) -> bool:
    """Return True when Chrome had to be started, False when it was already up."""
    if chrome_is_running(cdp_url):
        return False

    if not Path(CHROME_EXE).exists():
        raise ChromeUnavailable(f"Windows Chrome not found at {CHROME_EXE}")

    port = _port_of(cdp_url)
    subprocess.Popen(
        [
            CHROME_EXE,
            f"--remote-debugging-port={port}",
            # A dedicated profile is required: Chrome 136+ serves 404 on /json/*
            # when remote debugging points at the default user data directory.
            f"--user-data-dir={CHROME_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    if not quiet:
        print("Starting Chrome ...")
    deadline = time.monotonic() + CHROME_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if chrome_is_running(cdp_url, timeout=1.0):
            return True
        time.sleep(_POLL_SECONDS)

    raise ChromeUnavailable(
        f"Chrome did not expose CDP on {cdp_url} within {CHROME_TIMEOUT_SECONDS:.0f}s. "
        "Confirm networkingMode=mirrored is set in .wslconfig and that "
        "'wsl --shutdown' has been run since."
    )


def _pid_on_port(port: str) -> int | None:
    """Find whatever currently holds a local port, for stale-registry recovery."""
    listing = shutil.which("ss")
    if listing is None:
        return None
    result = subprocess.run(
        [listing, "-ltnp"], check=False, capture_output=True, text=True, timeout=10
    )
    for line in result.stdout.splitlines():
        if f":{port} " not in line:
            continue
        match = re.search(r"pid=(\d+)", line)
        if match:
            return int(match.group(1))
    return None


def stop_api(api_url: str, *, force: bool = False) -> bool:
    """Stop the API we started. With force, also stop whatever holds the port."""
    record = read_service("api")
    pid = record.pid if record else None
    if pid is None and force:
        pid = _pid_on_port(_port_of(api_url))
    if pid is None:
        forget_service("api")
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        forget_service("api")
        return False
    except PermissionError:
        forget_service("api")
        return False

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not is_running(api_url, timeout=1.0):
            break
        time.sleep(_POLL_SECONDS)
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)

    forget_service("api")
    return True


def windows_chrome_pids() -> list[int]:
    """PIDs of Windows chrome.exe processes using our dedicated profile.

    Matching on the profile path is what keeps the user's own Chrome windows
    alive; taskkill /IM chrome.exe would close every one of them.
    """
    if not Path(POWERSHELL).exists():
        return []
    script = (
        "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{Path(CHROME_PROFILE).name}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def stop_chrome(cdp_url: str) -> bool:
    """Stop the automation browser.

    Chrome runs as a Windows process, so the PID from Popen belongs to the WSL
    interop stub rather than to chrome.exe, and a Linux signal leaves the browser
    running. Kill the real Windows processes by PID instead.
    """
    pids = windows_chrome_pids()
    if not pids:
        forget_service("chrome")
        return False
    if Path(TASKKILL).exists():
        for pid in pids:
            subprocess.run(
                [TASKKILL, "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not chrome_is_running(cdp_url, timeout=1.0):
            break
        time.sleep(_POLL_SECONDS)
    forget_service("chrome")
    return True
