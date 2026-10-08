"""Local service control: what is running, and how to stop it."""

from __future__ import annotations

import subprocess
import time

from rich.table import Table

from personal_os_worker.core import service
from personal_os_worker.core.auth import (
    LoginError,
    Session,
    SupabaseAuth,
    active_session,
    load_session,
    login,
    save_session,
)
from personal_os_worker.core.config import CliConfig, load_cli_config
from personal_os_worker.core.service import ensure_chrome
from personal_os_worker.core.tables import console


def _uptime(started_at: float) -> str:
    seconds = int(time.time() - started_at)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def run_status() -> int:
    config = load_cli_config()
    table = Table(title="Personal OS services", header_style="bold")
    table.add_column("service")
    table.add_column("state")
    table.add_column("endpoint")
    table.add_column("pid", justify="right")
    table.add_column("uptime", justify="right")

    api_up = service.is_running(config.api_url)
    api_record = service.read_service("api")
    table.add_row(
        "api",
        "running" if api_up else "stopped",
        config.api_url,
        str(api_record.pid) if api_record and api_up else "-",
        _uptime(api_record.started_at) if api_record and api_up else "-",
    )

    chrome_up = service.chrome_is_running(config.cdp_url)
    chrome_pids = service.windows_chrome_pids() if chrome_up else []
    table.add_row(
        "chrome",
        "running" if chrome_up else "stopped",
        config.cdp_url,
        str(chrome_pids[0]) if chrome_pids else "-",
        "-",
    )
    console.print(table)

    session = load_session()
    if session is None:
        console.print("Not signed in. Run 'os login'.")
    else:
        state = "expired, will refresh" if session.expired else "valid"
        console.print(f"Signed in ({state}).")
    return 0


def run_stop(target: str, *, force: bool) -> int:
    config = load_cli_config()
    stopped: list[str] = []
    if target in {"api", "all"} and service.stop_api(config.api_url, force=force):
        stopped.append("api")
    if target in {"chrome", "all"} and service.stop_chrome(config.cdp_url):
        stopped.append("chrome")
    console.print(f"Stopped: {', '.join(stopped)}" if stopped else "Nothing was running.")
    return 0


def run_api(action: str) -> int:
    config = load_cli_config()
    if action in {"stop", "restart"}:
        service.stop_api(config.api_url, force=True)
    if action in {"start", "restart"}:
        service.ensure_running(config.api_url)
        console.print(f"API running at {config.api_url}")
        return 0
    if action == "logs":
        path = service.log_path()
        if not path.exists():
            console.print(f"No log yet at {path}")
            return 0
        subprocess.run(["tail", "-n", "80", str(path)], check=False)
        return 0
    console.print("API stopped.")
    return 0


def run_chrome(action: str) -> int:
    config = load_cli_config()
    if action in {"stop", "restart"}:
        service.stop_chrome(config.cdp_url)
    if action in {"start", "restart"}:
        service.ensure_chrome(config.cdp_url)
        console.print(f"Chrome exposing CDP at {config.cdp_url}")
        return 0
    console.print("Chrome stopped.")
    return 0


def supabase_auth(config: CliConfig) -> SupabaseAuth:
    return SupabaseAuth(url=config.supabase_url, publishable_key=config.supabase_publishable_key)


def sign_in(config: CliConfig) -> Session:
    """Run the Google sign-in in the automation browser and store the session."""
    # Login and autofill share one dedicated Chrome profile. Start it with CDP
    # from the outset; opening the profile first without remote debugging makes
    # a later `os chrome start` attach to that non-CDP process and time out.
    ensure_chrome(config.cdp_url)
    session = login(supabase_auth(config))
    console.print(f"Signed in. Session stored at {save_session(session)}")
    return session


def connected() -> tuple[CliConfig, str]:
    """Load configuration, guarantee the API is up, and return a fresh access token.

    Every command that talks to the API goes through here, so the API and the
    session are dependencies the CLI manages rather than steps to remember:
    with no stored session, or one that can no longer be refreshed, this signs
    in first and then carries on with the command.
    """
    config = load_cli_config()
    service.ensure_running(config.api_url)
    try:
        session = active_session(supabase_auth(config))
    except LoginError as error:
        console.print(f"{error} Signing in ...")
        session = sign_in(config)
    return config, session.access_token
