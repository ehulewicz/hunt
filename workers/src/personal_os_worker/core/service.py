"""Keep the local API running so the CLI is the only command to remember.

The API is a local dependency of the CLI, not something to manage by hand, so
commands that need it start it on demand and leave it running for the next one.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

API_EXECUTABLE = "personal-os-backend"
STARTUP_TIMEOUT_SECONDS = 45.0
_POLL_SECONDS = 0.25
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
TASKKILL = "/mnt/c/Windows/System32/taskkill.exe"

# Jobs drives the Windows Chrome build rather than the WSLg one: it renders
# properly and keeps the real profile, including the Google session. Reaching its
# CDP port from WSL needs networkingMode=mirrored in .wslconfig, because Chrome
# binds remote debugging to loopback only.
CHROME_EXE = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
CHROME_PROFILE_NAME = "personal-os-chrome"
CHROME_TIMEOUT_SECONDS = 30.0
_CHROME_LOCK = threading.Lock()
CMD = "/mnt/c/Windows/System32/cmd.exe"


class ApiUnavailable(RuntimeError):
    """Raised when the local API is neither running nor startable."""


class ChromeUnavailable(RuntimeError):
    """Raised when the automation browser is neither running nor startable."""


@functools.cache
def windows_home() -> str:
    """The Windows %USERPROFILE% of whoever runs this, resolved once.

    Every device has its own Windows user, so the dedicated Chrome profile is
    found from it rather than spelled out. `PERSONAL_OS_WINDOWS_HOME` names it
    outright where cmd.exe cannot be asked.
    """
    named = os.environ.get("PERSONAL_OS_WINDOWS_HOME", "").strip()
    if named:
        return named.rstrip("\\")
    if not Path(CMD).exists():
        raise ChromeUnavailable("This is not WSL on Windows, so there is no Windows profile")
    # cmd.exe complains about a WSL working directory, so ask it from C:.
    result = subprocess.run(
        [CMD, "/d", "/c", "echo %USERPROFILE%"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        cwd="/mnt/c",
    )
    home = result.stdout.strip()
    if result.returncode != 0 or not home or "%" in home:
        raise ChromeUnavailable("Could not read the Windows %USERPROFILE% through cmd.exe")
    return home.rstrip("\\")


def chrome_profile() -> str:
    """The automation Chrome's dedicated user-data directory, a Windows path."""
    return f"{windows_home()}\\{CHROME_PROFILE_NAME}"


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
    source: str | None = None  # fingerprint of the code the process was started from


def backend_source_fingerprint() -> str:
    """A digest of the backend source tree, so a pull is visible to the CLI."""
    root = Path(__file__).resolve().parents[4] / "backend" / "src"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        stat = path.stat()
        digest.update(f"{path.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def record_service(name: str, pid: int, port: str, *, source: str | None = None) -> None:
    """Track a service we started, so stopping it never has to guess."""
    path = registry_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"name": name, "pid": pid, "port": port, "started_at": time.time(), "source": source},
            indent=2,
        )
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
            source=str(data["source"]) if data.get("source") else None,
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
    port = urlparse(api_url).port
    return str(port) if port is not None else "8000"


def is_local(api_url: str) -> bool:
    """Whether the URL names an API on this machine, the only kind the CLI can manage."""
    return urlparse(api_url).hostname in {"127.0.0.1", "localhost", "::1"}


def ensure_running(api_url: str, *, quiet: bool = False) -> bool:
    """Return True when the API had to be started, False when it was already up.

    An API we started from older source is restarted: it runs the checked-out
    backend, so after a pull the old process would query tables and columns
    that no longer exist.

    A hosted API is only checked, never started or stopped: its deploy is not
    this machine's to manage.
    """
    if not is_local(api_url):
        if is_running(api_url):
            return False
        raise ApiUnavailable(f"The hosted API at {api_url} is not answering its health check.")
    source = backend_source_fingerprint()
    if is_running(api_url):
        record = read_service("api")
        if record is None or record.source == source:
            return False
        if not quiet:
            print("The backend source changed; restarting the local API ...")
        stop_api(api_url)

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
    record_service("api", process.pid, port, source=source)

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


def open_url(cdp_url: str, url: str) -> None:
    """Open a URL in the automation browser and leave the tab there.

    Under WSL `webbrowser.open` cannot reach Windows Chrome, so the only
    browser this machine can drive is the one `ensure_chrome` manages. Core
    already owns that browser's lifecycle, so it owns opening a page in it.

    Stopping Playwright does not close the tab: the browser was connected to,
    not launched by, this process.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        page = browser.contexts[0].new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)


def _port_refuses(cdp_url: str) -> bool:
    """Whether nothing at all listens on Chrome's port: the one sign it is not running."""
    parsed = urlparse(cdp_url)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 80), 1.0):
            return False
    except ConnectionRefusedError:
        return True
    except OSError:  # a timeout is a busy listener, not an absent one
        return False


def _wait_for_chrome(cdp_url: str, *, check_seconds: float, failure: str) -> None:
    deadline = time.monotonic() + CHROME_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if chrome_is_running(cdp_url, timeout=check_seconds):
            return
        time.sleep(_POLL_SECONDS)
    raise ChromeUnavailable(failure)


def ensure_chrome(cdp_url: str, *, quiet: bool = False) -> bool:
    """Return True when Chrome had to be started, False when it was already up.

    Chrome is launched only when nothing holds its port. One that holds it but is slow
    to answer is busy, not gone, and launching again would only open another window in
    it, which makes it slower still.
    """
    with _CHROME_LOCK:  # concurrent callers share one check rather than each launching
        return _ensure_chrome(cdp_url, quiet=quiet)


def _ensure_chrome(cdp_url: str, *, quiet: bool) -> bool:
    if chrome_is_running(cdp_url):
        return False

    port = _port_of(cdp_url)
    if not _port_refuses(cdp_url):
        _wait_for_chrome(
            cdp_url,
            check_seconds=5.0,
            failure=f"Chrome holds port {port} but is not answering; close some of its "
            "windows or run 'os chrome restart'",
        )
        return False

    if not Path(CHROME_EXE).exists():
        raise ChromeUnavailable(f"Windows Chrome not found at {CHROME_EXE}")

    subprocess.Popen(
        [
            CHROME_EXE,
            f"--remote-debugging-port={port}",
            # A dedicated profile is required: Chrome 136+ serves 404 on /json/*
            # when remote debugging points at the default user data directory.
            f"--user-data-dir={chrome_profile()}",
            "--no-first-run",
            "--no-default-browser-check",
            # The Hunt page streams this window from another machine, so it must
            # keep painting while covered, minimized, or behind a locked screen.
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    if not quiet:
        print("Starting Chrome ...")
    _wait_for_chrome(
        cdp_url,
        check_seconds=1.0,
        failure=f"Chrome did not expose CDP on {cdp_url} within {CHROME_TIMEOUT_SECONDS:.0f}s. "
        "Confirm networkingMode=mirrored is set in .wslconfig and that "
        "'wsl --shutdown' has been run since.",
    )
    return True


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
    if not is_local(api_url):
        # A record next to a hosted URL is left over from a failed local start,
        # and its PID may since belong to an unrelated process.
        forget_service("api")
        return False
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
        f"Where-Object {{ $_.CommandLine -like '*{CHROME_PROFILE_NAME}*' }} | "
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
