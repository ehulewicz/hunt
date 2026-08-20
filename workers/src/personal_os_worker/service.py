"""Keep the local API running so the CLI is the only command to remember.

The API is a local dependency of the CLI, not something to manage by hand, so
commands that need it start it on demand and leave it running for the next one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx

API_EXECUTABLE = "personal-os-api"
STARTUP_TIMEOUT_SECONDS = 45.0
_POLL_SECONDS = 0.25

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


def log_path() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".local" / "state"
    return base / "personal-os" / "api.log"


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
    handle = destination.open("a")
    try:
        subprocess.Popen(
            [executable, "--port", _port_of(api_url)],
            stdout=handle,
            stderr=handle,
            stdin=subprocess.DEVNULL,
            # Detach so the API outlives this CLI invocation and is reused by the next.
            start_new_session=True,
        )
    finally:
        handle.close()

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
