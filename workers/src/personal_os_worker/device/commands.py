"""`os device install | serve | status`."""

from __future__ import annotations

import os
import sys

import httpx

from personal_os_worker.core import service
from personal_os_worker.core.commands import connected
from personal_os_worker.core.config import load_cli_config
from personal_os_worker.core.service import state_dir
from personal_os_worker.core.tables import console
from personal_os_worker.device.heartbeat import Presence, doctor_checks, readiness
from personal_os_worker.device.identity import (
    AGENT_PORT,
    DeviceError,
    ensure_identity,
    load_identity,
    tailnet_url,
    windows_machine_name,
)
from personal_os_worker.device.install import register_logon_task, serve_on_tailnet, wsl_distro
from personal_os_worker.device.update import read_checkout, repo_root, update_checkout

AGENT_URL = f"http://127.0.0.1:{AGENT_PORT}"
LOG_LIMIT = 5_000_000


def agent_running() -> bool:
    try:
        return httpx.get(f"{AGENT_URL}/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def run_install() -> int:
    """Make this machine a device: identity, tailnet address, logon task, doctor."""
    config, token = connected()
    try:
        url = tailnet_url()
    except DeviceError as error:
        raise DeviceError(
            f"{error}. Install Tailscale for Windows, sign in, and turn on MagicDNS and "
            "HTTPS certificates for the tailnet first."
        ) from None
    identity = ensure_identity(name=windows_machine_name(), url=url)
    console.print(f"Device [bold]{identity.name}[/bold] ({identity.id[:8]}) at {url}")
    serve_on_tailnet()
    console.print(f"  tailscale serve: {url} -> 127.0.0.1:{AGENT_PORT}, tailnet only")
    register_logon_task(wsl_distro())
    console.print("  logon task registered; the agent starts at every Windows sign-in, and now")
    checks = doctor_checks(config, token)
    for check in checks:
        console.print(f"  [{'OK' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    ready, problem = readiness(checks)
    console.print("Ready." if ready else f"Not ready yet: {problem}")
    return 0 if ready else 1


def _log_to_file() -> None:
    """Unattended, the agent's output goes to device.log, rotated at 5 MB."""
    path = state_dir() / "device.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_LIMIT:
        path.replace(path.with_suffix(".log.1"))
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.dup2(handle, 1)
    os.dup2(handle, 2)
    os.close(handle)


def run_serve() -> int:
    """The agent itself; the logon task runs this."""
    import uvicorn

    from personal_os_worker.device.server import Agent, TokenVerifier, create_app, reexec

    identity = load_identity()
    if identity is None:
        raise DeviceError("This machine is not a device yet; run 'os device install'.")
    if agent_running():
        console.print("The device agent is already running.")
        return 0
    if not sys.stdout.isatty():
        _log_to_file()
    config = load_cli_config()
    repo = repo_root()
    update = update_checkout(repo)
    console.print(f"device agent: {update.note}")
    if update.pulled_from is not None:
        reexec()  # into the code just pulled
    agent = Agent(
        config,
        identity,
        presence=Presence(config, identity, repo=repo),
        verifier=TokenVerifier(
            config.api_url,
            ensure_api=lambda: service.ensure_running(config.api_url, quiet=True),
        ),
        update=lambda: update_checkout(repo).note,
        ensure_chrome=lambda: service.ensure_chrome(config.cdp_url, quiet=True),
        log_dir=state_dir() / "runs",
    )
    console.print(f"device agent: {identity.name} listening on 127.0.0.1:{AGENT_PORT}")
    uvicorn.run(
        create_app(agent),
        host="127.0.0.1",
        port=AGENT_PORT,
        ws="websockets-sansio",
        log_level="warning",
    )
    return 0


def run_status() -> int:
    identity = load_identity()
    if identity is None:
        console.print("Not a device yet. Run 'os device install'.")
        return 1
    console.print(f"[bold]{identity.name}[/bold]  {identity.id}")
    console.print(f"  address   {identity.url or 'none yet'}")
    checkout = read_checkout(repo_root())
    changes = "  [yellow]running local changes[/yellow]" if checkout.local_changes else ""
    console.print(f"  commit    {checkout.short} on {checkout.branch}{changes}")
    if not agent_running():
        console.print("  agent     [red]not running[/red] (start it: os device serve)")
        return 1
    _, token = connected()
    response = httpx.get(
        f"{AGENT_URL}/status", headers={"Authorization": f"Bearer {token}"}, timeout=30.0
    )
    response.raise_for_status()
    status = response.json()
    device = status.get("device") or {}
    console.print(
        "  agent     running, "
        + ("ready" if device.get("ready") else f"not ready: {device.get('problem')}")
    )
    run = status.get("run")
    if run:
        what = " — ".join(part for part in (run.get("company"), run.get("title")) if part)
        console.print(f"  run       {run.get('phase')} {what or run.get('reference') or ''}")
    return 0
