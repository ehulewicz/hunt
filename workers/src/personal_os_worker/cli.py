"""The `os` command: one entry point for every local Personal OS domain.

Domains are subcommands rather than separate binaries, so `learning` and `life`
can be added later without claiming more names on PATH.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import httpx

from personal_os_worker import __version__
from personal_os_worker.auth import LoginError
from personal_os_worker.commands import hunt, services
from personal_os_worker.commands.hunt import DEFAULT_TECHNICAL_FAMILIES
from personal_os_worker.config import load_cli_config
from personal_os_worker.resume import build_resume, watch_resume
from personal_os_worker.service import ApiUnavailable, ChromeUnavailable
from personal_os_worker.tables import ReferenceError, console


def listing_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("limit must be an integer") from None
    if parsed < 1:
        raise argparse.ArgumentTypeError("limit must be at least 1")
    return parsed


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

    listings = jobs_commands.add_parser("list", help="Browse open listings")
    category = listings.add_mutually_exclusive_group()
    category.add_argument("--faang", action="store_true", help="Only FAANG+ listings")
    category.add_argument("--quant", action="store_true", help="Only quant listings")
    listings.add_argument(
        "--location", action="append", default=[], help="Filter by normalized location; repeatable"
    )
    listings.add_argument(
        "--family",
        action="append",
        default=[],
        choices=DEFAULT_TECHNICAL_FAMILIES,
        help="Technical role family; repeatable",
    )
    listings.add_argument(
        "--all-roles",
        action="store_true",
        help="Include listings classified as other",
    )
    listings.add_argument(
        "--limit",
        type=listing_limit,
        default=50,
        help="Rows to show (1 or more; overshoots return all available)",
    )

    jobs_commands.add_parser("sync", help="Deterministically sync the configured listing feeds")

    open_listing = jobs_commands.add_parser(
        "open", help="Open a displayed listing in the host browser"
    )
    open_listing.add_argument("ref", help="Row number from the last list table or id prefix")
    open_listing.add_argument(
        "--source",
        action="store_true",
        help="Open the discovery source instead of the direct application URL",
    )

    jobs_commands.add_parser("apps", help="Show applications and their progress")

    answers = jobs_commands.add_parser("answers", help="Manage reusable long-form answers")
    answer_commands = answers.add_subparsers(dest="answer_command", required=True)
    answer_commands.add_parser("list", help="List canonical reusable answers")
    answer_add = answer_commands.add_parser("add", help="Add a canonical answer")
    answer_add.add_argument("--question", required=True)
    answer_add_input = answer_add.add_mutually_exclusive_group(required=True)
    answer_add_input.add_argument("--text", help="Answer wording")
    answer_add_input.add_argument("--file", help="Read answer wording from a UTF-8 file")
    answer_add.add_argument("--draft", action="store_true", help="Save unapproved")
    answer_add.add_argument("--reuse", choices=["exact", "contextual", "pattern"], default="exact")
    answer_edit = answer_commands.add_parser("edit", help="Edit a listed answer")
    answer_edit.add_argument("ref", help="Row number from answers list or id prefix")
    answer_edit.add_argument("--question")
    answer_edit_input = answer_edit.add_mutually_exclusive_group()
    answer_edit_input.add_argument("--text", help="Replacement wording")
    answer_edit_input.add_argument("--file", help="Read replacement wording from a UTF-8 file")
    answer_approval = answer_edit.add_mutually_exclusive_group()
    answer_approval.add_argument("--approve", action="store_const", const=True, dest="approved")
    answer_approval.add_argument("--draft", action="store_const", const=False, dest="approved")
    answer_edit.set_defaults(approved=None)
    answer_edit.add_argument("--reuse", choices=["exact", "contextual", "pattern"])

    resume = jobs_commands.add_parser("resume", help="Build or watch the canonical one-page resume")
    resume_commands = resume.add_subparsers(dest="resume_command", required=True)
    resume_build = resume_commands.add_parser("build", help="Compile resume JSON to PDF")
    resume_build.add_argument("--input", help="Resume JSON path; defaults to canonical main")
    resume_build.add_argument("--output", help="Stable PDF output path")
    resume_watch = resume_commands.add_parser(
        "watch", help="Recompile whenever the canonical JSON changes"
    )
    resume_watch.add_argument("--input", help="Resume JSON path; defaults to canonical main")
    resume_watch.add_argument("--output", help="Stable PDF output path")
    resume_watch.add_argument(
        "--open", action="store_true", help="Open an auto-refreshing PDF preview"
    )
    resume_watch.add_argument("--interval", type=float, default=0.5)

    prep = jobs_commands.add_parser("prep", help="Queue preparation for listings")
    prep.add_argument("refs", nargs="+", help="Row numbers from the last listing, or id prefixes")

    autofill = jobs_commands.add_parser(
        "autofill",
        help="Fill a saved listing in native Chrome, or manage captured failures",
    )
    autofill.add_argument("ref", help="Listing row/id, or the literal 'failures'")
    autofill.add_argument(
        "failure_action",
        nargs="?",
        choices=["list", "show", "diagnose", "draft-fix", "apply-fix", "verify"],
        help="Failure command when ref is 'failures'",
    )
    autofill.add_argument("failure_ref", nargs="?", help="Failure row or id prefix")
    autofill.add_argument(
        "verify_listing_ref", nargs="?", help="Saved listing used for live verification"
    )
    autofill.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be filled without touching the page",
    )
    autofill.add_argument(
        "--no-claude",
        action="store_true",
        help="Skip the bounded Claude pass over remaining short and choice questions",
    )

    run = jobs_commands.add_parser("run", help="Run every queued task, then exit")
    run_mode = run.add_mutually_exclusive_group()
    run_mode.add_argument("--once", action="store_true", help="Stop after a single task")
    run_mode.add_argument(
        "--watch", action="store_true", help="Keep polling for new tasks instead of exiting"
    )
    return parser


def run_login() -> int:
    services.sign_in(load_cli_config())
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
        if args.hunt_command == "list":
            category = "faang" if args.faang else "quant" if args.quant else None
            role_families = (
                [] if args.all_roles else args.family or list(DEFAULT_TECHNICAL_FAMILIES)
            )
            return hunt.run_listings(
                category=category,
                locations=args.location,
                role_families=role_families,
                limit=args.limit,
            )
        if args.hunt_command == "sync":
            return hunt.run_sync()
        if args.hunt_command == "open":
            return hunt.run_open_listing(args.ref, source=args.source)
        if args.hunt_command == "apps":
            return hunt.run_apps()
        if args.hunt_command == "answers":
            if args.answer_command == "list":
                return hunt.run_answers()
            if args.answer_command == "add":
                return hunt.run_answer_add(
                    question=args.question,
                    text=args.text,
                    file=args.file,
                    approved=not args.draft,
                    reuse=args.reuse,
                )
            if args.answer_command == "edit":
                return hunt.run_answer_edit(
                    args.ref,
                    question=args.question,
                    text=args.text,
                    file=args.file,
                    approved=args.approved,
                    reuse=args.reuse,
                )
        if args.hunt_command == "resume":
            source = Path(args.input).expanduser() if args.input else None
            output = Path(args.output).expanduser() if args.output else None
            if args.resume_command == "build":
                result = build_resume(source, output)
                console.print(f"Resume built: {result.pdf}")
                return 0
            if args.resume_command == "watch":
                watch_resume(
                    source,
                    output,
                    interval=args.interval,
                    open_preview=args.open,
                )
                return 0
        if args.hunt_command == "prep":
            return hunt.run_prep(args.refs)
        if args.hunt_command == "autofill":
            if args.ref == "failures":
                return hunt.run_autofill_failure_command(
                    args.failure_action,
                    args.failure_ref,
                    args.verify_listing_ref,
                )
            if args.failure_action or args.failure_ref or args.verify_listing_ref:
                raise RuntimeError("Unexpected autofill arguments")
            return hunt.run_autofill(args.ref, dry_run=args.dry_run, no_claude=args.no_claude)
        if args.hunt_command == "run":
            return hunt.run_tasks(once=args.once, watch=args.watch)
    return 2


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    try:
        return _dispatch(args)
    except httpx.HTTPStatusError as error:
        response = error.response
        try:
            payload = response.json()
        except ValueError:
            payload = None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if not isinstance(detail, str) or not detail.strip():
            detail = response.text.strip() or response.reason_phrase
        raise SystemExit(
            f"API request failed ({response.status_code}) for "
            f"{error.request.method} {error.request.url.path}: {detail}"
        ) from None
    except (
        LoginError,
        ApiUnavailable,
        ChromeUnavailable,
        ReferenceError,
        RuntimeError,
    ) as error:
        raise SystemExit(str(error)) from None
