"""The `os` command: one entry point for every local Personal OS domain.

Domains are subcommands rather than separate binaries, so `learning` and `life`
can be added later without claiming more names on PATH.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import httpx

from personal_os_worker import __version__
from personal_os_worker.auth import LoginError, login, save_session
from personal_os_worker.commands import hunt, services
from personal_os_worker.config import load_cli_config
from personal_os_worker.service import ApiUnavailable, ChromeUnavailable
from personal_os_worker.tables import ReferenceError, console


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="os",
        description="Personal OS local command line.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="Show which local services are running")

    stop = commands.add_parser("stop", help="Stop local services this CLI started")
    stop.add_argument("target", nargs="?", default="all", choices=["api", "chrome", "all"])
    stop.add_argument(
        "--force",
        action="store_true",
        help="Also stop whatever holds the port when the record is stale",
    )

    api = commands.add_parser("api", help="Control the local API")
    api.add_argument("action", choices=["start", "stop", "restart", "logs"])

    chrome = commands.add_parser("chrome", help="Control the automation browser")
    chrome.add_argument("action", choices=["start", "stop", "restart"])

    commands.add_parser("login", help="Sign in with Google through Supabase")
    commands.add_parser("whoami", help="Show the signed-in account")
    commands.add_parser("doctor", help="Check sign-in, services, Claude, and PDF tools")

    jobs = commands.add_parser("hunt", help="Jobs: listings, applications, preparation")
    jobs_commands = jobs.add_subparsers(dest="hunt_command", required=True)

    listings = jobs_commands.add_parser("listings", help="Browse open listings")
    category = listings.add_mutually_exclusive_group()
    category.add_argument("--faang", action="store_true", help="Only FAANG+ listings")
    category.add_argument("--quant", action="store_true", help="Only quant listings")
    listings.add_argument(
        "--location", action="append", default=[], help="Filter by normalized location; repeatable"
    )
    listings.add_argument("--limit", type=int, default=50)

    jobs_commands.add_parser("apps", help="Show applications and their progress")

    prep = jobs_commands.add_parser("prep", help="Queue preparation for listings")
    prep.add_argument("refs", nargs="+", help="Row numbers from the last listing, or id prefixes")

    run = jobs_commands.add_parser("run", help="Execute queued tasks in the foreground")
    run.add_argument("--once", action="store_true", help="Exit after one task or empty poll")
    return parser


def run_login() -> int:
    config = load_cli_config()
    session = login(services.supabase_auth(config))
    console.print(f"Signed in. Session stored at {save_session(session)}")
    return 0


def run_whoami() -> int:
    config, access_token = services.connected()
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
    console.print(f"{account.get('display_name') or account.get('email')} <{account.get('email')}>")
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "status":
        return services.run_status()
    if args.command == "stop":
        return services.run_stop(args.target, force=args.force)
    if args.command == "api":
        return services.run_api(args.action)
    if args.command == "chrome":
        return services.run_chrome(args.action)
    if args.command == "login":
        return run_login()
    if args.command == "whoami":
        return run_whoami()
    if args.command == "doctor":
        return hunt.run_doctor_command()
    if args.command == "hunt":
        if args.hunt_command == "listings":
            category = "faang" if args.faang else "quant" if args.quant else None
            return hunt.run_listings(
                category=category, locations=args.location, limit=args.limit
            )
        if args.hunt_command == "apps":
            return hunt.run_apps()
        if args.hunt_command == "prep":
            return hunt.run_prep(args.refs)
        if args.hunt_command == "run":
            return hunt.run_tasks(once=args.once)
    return 2


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    try:
        return _dispatch(args)
    except (
        LoginError,
        ApiUnavailable,
        ChromeUnavailable,
        ReferenceError,
        RuntimeError,
    ) as error:
        raise SystemExit(str(error)) from None
