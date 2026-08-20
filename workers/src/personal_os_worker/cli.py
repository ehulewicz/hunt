from __future__ import annotations

import argparse
import getpass
from collections.abc import Sequence

from personal_os_worker import __version__
from personal_os_worker.auth import (
    LoginError,
    SupabaseAuth,
    active_session,
    login,
    save_session,
)
from personal_os_worker.config import WorkerConfig, load_cli_config, load_config, save_config
from personal_os_worker.doctor import run_doctor
from personal_os_worker.runner import run_worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hunt",
        description="Foreground Personal OS Jobs worker. It never submits applications.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    pair = commands.add_parser("pair", help="Store a revocable worker token locally")
    pair.add_argument("--api-url", required=True)
    pair.add_argument("--cdp-url", default="http://127.0.0.1:9333")

    commands.add_parser("login", help="Sign in with Google through Supabase")
    commands.add_parser("whoami", help="Show the signed-in account")

    run = commands.add_parser("run", help="Claim and execute Jobs tasks in the foreground")
    run.add_argument("--once", action="store_true", help="Exit after one task or empty poll")
    commands.add_parser("doctor", help="Check pairing, Chrome, Claude, and PDF tools")
    return parser


def supabase_auth() -> SupabaseAuth:
    config = load_cli_config()
    return SupabaseAuth(
        url=config.supabase_url,
        publishable_key=config.supabase_publishable_key,
    )


def run_login() -> int:
    try:
        session = login(supabase_auth())
    except (LoginError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
    path = save_session(session)
    print(f"Signed in. Session stored at {path}")
    return 0


def run_whoami() -> int:
    import httpx

    try:
        config = load_cli_config()
        session = active_session(supabase_auth())
    except (LoginError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
    response = httpx.get(
        f"{config.api_url}/v1/me",
        headers={"Authorization": f"Bearer {session.access_token}"},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise SystemExit(
            f"API rejected the session with {response.status_code}: {response.text[:200]}"
        )
    account = response.json()
    print(f"{account.get('display_name') or account.get('email')} <{account.get('email')}>")
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    if args.command == "pair":
        token = getpass.getpass("Worker token (input hidden): ").strip()
        if not token:
            raise SystemExit("Worker token is required")
        path = save_config(
            WorkerConfig(
                api_url=args.api_url.rstrip("/"),
                token=token,
                cdp_url=args.cdp_url,
            )
        )
        print(f"Worker paired. Configuration stored at {path}")
        return 0
    if args.command == "login":
        return run_login()
    if args.command == "whoami":
        return run_whoami()
    try:
        if args.command == "run":
            return run_worker(load_config(), once=args.once)
        if args.command == "doctor":
            checks = run_doctor(load_config())
            for check in checks:
                marker = "OK" if check.ok else "FAIL"
                print(f"[{marker}] {check.name}: {check.detail}")
            return 0 if all(check.ok for check in checks) else 1
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    return 2
