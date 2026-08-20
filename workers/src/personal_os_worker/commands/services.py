"""Local service control: what is running, and how to stop it."""

from __future__ import annotations

import subprocess
import time

from rich.table import Table

from personal_os_worker import service
from personal_os_worker.auth import SupabaseAuth, active_session, load_session
from personal_os_worker.config import CliConfig, load_cli_config
from personal_os_worker.tables import console


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


def connected() -> tuple[CliConfig, str]:
    """Load configuration, guarantee the API is up, and return a fresh access token.

    Every command that talks to the API goes through here, so the API is a
    dependency the CLI manages rather than a second process to remember.
    """
    config = load_cli_config()
    service.ensure_running(config.api_url)
    session = active_session(supabase_auth(config))
    return config, session.access_token
