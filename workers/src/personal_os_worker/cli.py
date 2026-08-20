from __future__ import annotations

import argparse
from collections.abc import Sequence

import httpx

from personal_os_worker import __version__
from personal_os_worker.auth import (
    LoginError,
    SupabaseAuth,
    active_session,
    login,
    save_session,
)
from personal_os_worker.config import CliConfig, load_cli_config
from personal_os_worker.doctor import run_doctor
from personal_os_worker.runner import run_worker
from personal_os_worker.service import (
    ApiUnavailable,
    ChromeUnavailable,
    ensure_chrome,
    ensure_running,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hunt",
        description="Local Jobs CLI. It never submits applications.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("login", help="Sign in with Google through Supabase")
    commands.add_parser("whoami", help="Show the signed-in account")

    run = commands.add_parser("run", help="Execute queued Jobs tasks in the foreground")
    run.add_argument("--once", action="store_true", help="Exit after one task or empty poll")
    commands.add_parser("doctor", help="Check sign-in, the API, Chrome, Claude, and PDF tools")
    return parser


def supabase_auth(config: CliConfig) -> SupabaseAuth:
    return SupabaseAuth(url=config.supabase_url, publishable_key=config.supabase_publishable_key)


def connected() -> tuple[CliConfig, str]:
    """Load configuration, guarantee the API is up, and return a fresh access token.

    Every command that talks to the API goes through here, so the API is a
    dependency the CLI manages rather than a second process to remember.
    """
    config = load_cli_config()
    ensure_running(config.api_url)
    session = active_session(supabase_auth(config))
    return config, session.access_token


def run_login() -> int:
    config = load_cli_config()
    session = login(supabase_auth(config))
    path = save_session(session)
    print(f"Signed in. Session stored at {path}")
    return 0


def run_whoami() -> int:
    config, access_token = connected()
    response = httpx.get(
        f"{config.api_url}/v1/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise SystemExit(
            f"API rejected the session with {response.status_code}: {response.text[:200]}"
        )
    account = response.json()
    print(f"{account.get('display_name') or account.get('email')} <{account.get('email')}>")
    return 0


def run_doctor_command() -> int:
    config, access_token = connected()
    checks = run_doctor(config, access_token)
    for check in checks:
        print(f"[{'OK' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    try:
        if args.command == "login":
            return run_login()
        if args.command == "whoami":
            return run_whoami()
        if args.command == "doctor":
            return run_doctor_command()
        if args.command == "run":
            config, access_token = connected()
            # Tasks drive the visible browser, so Chrome is a dependency too.
            ensure_chrome(config.cdp_url)
            return run_worker(config, access_token, once=args.once)
    except (LoginError, ApiUnavailable, ChromeUnavailable, RuntimeError) as error:
        raise SystemExit(str(error)) from None
    return 2
