"""The `os` command: one entry point for every local Personal OS domain.

Domains are subcommands rather than separate binaries, so `learning` and `life`
can be added later without claiming more names on PATH.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import httpx

from personal_os_worker import __version__
from personal_os_worker.core import commands as services
from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.auth import LoginError
from personal_os_worker.core.config import load_cli_config
from personal_os_worker.core.service import ApiUnavailable, ChromeUnavailable
from personal_os_worker.core.tables import ReferenceError, console
from personal_os_worker.core.tls import tls
from personal_os_worker.device import commands as devices
from personal_os_worker.documents import resume as resumes
from personal_os_worker.jobs import commands as hunt
from personal_os_worker.jobs import history, inbox, knowledge
from personal_os_worker.jobs.commands import APPLICATION_STATUSES, DEFAULT_TECHNICAL_FAMILIES
from personal_os_worker.jobs.facts import commands as facts
from personal_os_worker.jobs.facts.commands import VALUE_TYPES
from personal_os_worker.jobs.repair import commands as repairs
from personal_os_worker.jobs.sites import ATS_NAMES

HUNT_COMMANDS = (
    "today", "sync", "open", "apps", "skipped", "inbox", "patterns", "facts", "prose",
    "archive", "resume", "apply", "repair", "validate", "replay", "changes", "fixes", "sites",
)
LISTING_FLAGS = ("--faang", "--quant", "--location", "--family", "--all-roles", "--ats")


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

    device = commands.add_parser(
        "device", help="This machine as a Hunt device the Vercel page can see and drive"
    )
    device.add_argument(
        "action",
        choices=["install", "serve", "status"],
        help="install: set up once; serve: run the agent (the logon task does); status",
    )

    commands.add_parser("login", help="Sign in with Google through Supabase")
    commands.add_parser("whoami", help="Show the signed-in account")
    commands.add_parser("doctor", help="Check sign-in, services, Claude, and PDF tools")

    jobs = commands.add_parser(
        "hunt",
        help="Jobs: listings, applications, what is learned, and the resume",
        description=(
            "os hunt [N] lists open listings (N rows, default 10). "
            "os hunt inbox reviews what was learned; os hunt resume edits the resume."
        ),
        usage=(
            "os hunt [N] [--faang | --quant] [--location L] [--family F] [--all-roles] [--ats A]\n"
            "       os hunt {" + ",".join(HUNT_COMMANDS) + "} ..."
        ),
    )
    jobs_commands = jobs.add_subparsers(
        dest="hunt_command", required=True, metavar="{" + ",".join(HUNT_COMMANDS) + "}"
    )

    # `list` is how `os hunt [N]` is spelled internally; it is not offered by name.
    listings = jobs_commands.add_parser("list")
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
    listings.add_argument("--ats", choices=ATS_NAMES, help="Only this form type")
    listings.add_argument(
        "limit",
        nargs="?",
        type=listing_limit,
        default=10,
        help="Rows to show (1 or more; overshoots return all available)",
    )

    today = jobs_commands.add_parser(
        "today", help="Listings posted today or yesterday that you have not applied to"
    )
    today.add_argument(
        "--all-roles", action="store_true", help="Include listings classified as other"
    )
    today.add_argument("--ats", choices=ATS_NAMES, help="Only this form type")

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

    skipped = jobs_commands.add_parser(
        "skipped", help="Listings passed over with Ctrl+D; 'restore <n>' returns one to the board"
    )
    skipped.add_argument("action", nargs="?", choices=["restore"])
    skipped.add_argument("ref", nargs="?", help="Row number from the skipped table or id prefix")

    apps = jobs_commands.add_parser("apps", help="Track applications: list, or mark one")
    apps.add_argument("ref", nargs="?", help="Row number from the last apps table or id prefix")
    apps.add_argument(
        "status",
        nargs="?",
        choices=APPLICATION_STATUSES,
        help="New status for that application",
    )

    jobs_commands.add_parser(
        "inbox", help="Accept or reject what was learned; bind questions to facts"
    )
    patterns = jobs_commands.add_parser(
        "patterns", help="Which questions fill deterministically, and which wait"
    )
    patterns.add_argument(
        "--all", action="store_true", dest="every", help="Include rejected and retired patterns"
    )
    patterns.add_argument(
        "--check",
        action="store_true",
        help="Check active expressions against every recorded question and "
        "list where they disagree (exits 1 when any conflict is found)",
    )
    prose = jobs_commands.add_parser("prose", help="Saved long-form writing")
    prose.add_argument("action", nargs="?", choices=["list", "add", "approve", "delete"])
    prose.add_argument("ref", nargs="?", help="Row number from 'os hunt prose' or id prefix")
    prose.add_argument("--question", help="The question the writing answers (add)")
    prose.add_argument("--file", help="Read the writing from a UTF-8 file (add)")
    archive = jobs_commands.add_parser(
        "archive", help="The old system, frozen: answers, retired rules, long-form writing"
    )
    archive.add_argument("section", nargs="?", default="answers",
                         choices=["answers", "rules", "prose"])
    archive.add_argument("--markdown", help="Write archived long-form answers to this file")

    facts_parser = jobs_commands.add_parser(
        "facts", help="List, review, set, or delete what is true about you"
    )
    fact_commands = facts_parser.add_subparsers(dest="fact_command", required=True)
    fact_commands.add_parser("list", help="Stored and derived facts")
    fact_commands.add_parser("review", help="Accept, edit, or delete each unverified fact")
    facts_delete = fact_commands.add_parser("delete", help="Delete one stored fact")
    facts_delete.add_argument("key", help="Dotted key from 'os hunt facts list'")
    facts_delete.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    facts_set = fact_commands.add_parser("set", help="Set one fact; setting it verifies it")
    facts_set.add_argument("key", help="Dotted fact key, e.g. education.graduation_date")
    facts_set.add_argument("value", nargs="?", help="Value; JSON for list and object facts")
    facts_set.add_argument(
        "--none",
        action="store_true",
        help="The answer is none: store the empty value of its type, so the field stays blank",
    )
    facts_set.add_argument("--type", choices=VALUE_TYPES, dest="value_type")
    facts_set.add_argument(
        "--label", help="Plain-language description, required for a fact the catalog lacks"
    )

    resume = jobs_commands.add_parser(
        "resume", help="Read or write the career record's resume document"
    )
    resume_commands = resume.add_subparsers(dest="resume_command", required=True)
    resume_commands.add_parser(
        "get", help="Print the record's resume document as JSON to stdout"
    )
    resume_put = resume_commands.add_parser(
        "put", help="Save a resume JSON file as the career record"
    )
    resume_put.add_argument("file", help="The resume document JSON to save")

    autofill = jobs_commands.add_parser(
        "apply",
        help="Fill a listing in native Chrome, or manage captured failures",
    )
    autofill.add_argument(
        "ref",
        nargs="?",
        help="Listing row/id, a posting URL (a referral link too), 'today', or 'failures'; "
        "omitted = the top board row",
    )
    autofill.add_argument(
        "--ats", choices=ATS_NAMES, help="Top row on this form type (no row given, or 'today')"
    )
    autofill.add_argument(
        "failure_action",
        nargs="?",
        choices=["list", "show"],
        help="Failure command when ref is 'failures'",
    )
    autofill.add_argument("failure_ref", nargs="?", help="Failure row or id prefix")
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
    # Repair is how a fill gets a second chance on the page it already has, so it
    # is on for every application; --repair stays accepted and does nothing.
    autofill.add_argument("--repair", dest="repair", action="store_true", default=True,
                          help=argparse.SUPPRESS)
    autofill.add_argument(
        "--no-repair",
        dest="repair",
        action="store_false",
        help="Leave a failed field for you instead of retrying it in place",
    )
    autofill.add_argument(
        "--fresh",
        action="store_true",
        help="Open a new tab instead of continuing on the one already on this application",
    )
    autofill.add_argument(
        "--explain",
        action="store_true",
        help="Print every control's whole decision: control, question, planner, resolver, "
        "operation",
    )
    autofill.add_argument(
        "--step",
        action="store_true",
        help="Explain, and pause before each control is filled (Enter fills, s skips, "
        "c stops pausing)",
    )
    autofill.add_argument(
        "--referred",
        nargs="?",
        const="",
        metavar="NAME",
        help="This application came through a referral; NAME is who referred you",
    )
    autofill.add_argument("--company", help="Company, for a posting URL that is not on the board")
    autofill.add_argument("--title", help="Role title, for a posting URL that is not on the board")
    autofill.add_argument(
        "--ask-facts",
        action="store_true",
        help="When a question needs a fact you have not set, ask for it here and fill it in",
    )
    autofill.add_argument(
        "--done",
        action="store_true",
        help="Do not fill anything; record that you applied to this listing yourself",
    )
    autofill.add_argument(
        "--accept-letter",
        action="store_true",
        help="Use the filled cover letter as is, without review, when no [slot] prompt is left",
    )
    autofill.add_argument(
        "--remote",
        action="store_true",
        help="Driven from the Hunt page through this device's agent (the agent passes this)",
    )

    repair = jobs_commands.add_parser(
        "repair", help="Retry earlier failed fields in an isolated browser; never submits"
    )
    repair.add_argument(
        "--watch", action="store_true", help="Keep draining the queue as failures arrive"
    )
    repair.add_argument("--until", type=int, help=argparse.SUPPRESS)
    jobs_commands.add_parser(
        "validate", help="Watch each repaired rule fill its field, then accept or reject it"
    )
    replay = jobs_commands.add_parser(
        "replay",
        help="Replay the code as it stands against the answers you accepted; "
        "exits 1 when one changes for the worse",
    )
    replay.add_argument("--question", help="The failed question a code change fixes")
    replay.add_argument("--fact", dest="fact_key", help="The fact that question is filled from")
    replay.add_argument(
        "--json", action="store_true", dest="json_line",
        help="End with one JSON line of the counts, for the fixer",
    )

    changes = jobs_commands.add_parser(
        "changes",
        help="Every change to a pattern, fact, prose, question, strategy, or code: "
        "who made it, what it fixed, how its replay went",
    )
    changes.add_argument(
        "action", nargs="?", metavar="N|show",
        help="Rows to show (default 20), or 'show' to print one in full",
    )
    changes.add_argument("ref", nargs="?", help="Row number from 'os hunt changes' or id prefix")
    changes.add_argument("--subject", choices=history.CHANGE_SUBJECTS)
    changes.add_argument("--status", dest="change_status", choices=history.CHANGE_STATUSES)
    fixes = jobs_commands.add_parser(
        "fixes", help="Failures the fixer took on: being fixed, fixed by a commit, or unsolved"
    )
    fixes.add_argument(
        "--retry", metavar="N|ID",
        help="Put an unsolved problem row (a fixes row or id prefix) back on the queue",
    )
    jobs_commands.add_parser(
        "sites", help="Each site's latest run and its model calls (0 meets the goal)"
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
        verify=tls(),
    )
    if response.status_code != 200:
        raise SystemExit(
            f"API rejected the session with {response.status_code}: {response.text[:200]}"
        )
    account = response.json()
    console.print(f"{account.get('display_name') or account.get('email')} <{account.get('email')}>")
    return 0


def run_resume(args: argparse.Namespace) -> int:
    config, token = services.connected()
    api = WorkerApi(config, token)
    try:
        if args.resume_command == "get":
            document = api.career().get("document")
            if not isinstance(document, dict):
                raise RuntimeError("The career record response carried no document")
            # Plain stdout, so `os hunt resume get > file` writes only the JSON.
            sys.stdout.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
            return 0
        source = Path(args.file).expanduser()
        try:
            document = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            console.print(
                f"Resume JSON is invalid at line {error.lineno}, "
                f"column {error.colno}: {error.msg}",
                markup=False,
            )
            return 1
        try:
            api.put_career(document)
        except httpx.HTTPStatusError as error:
            console.print(f"Resume not saved: {resumes.rejection(error)}", markup=False)
            return 1
        console.print("Resume saved; /resume serves the new PDF.")
        return 0
    finally:
        api.close()


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "status":
        return services.run_status()
    if args.command == "stop":
        return services.run_stop(args.target, force=args.force)
    if args.command == "api":
        return services.run_api(args.action)
    if args.command == "chrome":
        return services.run_chrome(args.action)
    if args.command == "device":
        if args.action == "install":
            return devices.run_install()
        if args.action == "serve":
            return devices.run_serve()
        return devices.run_status()
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
                ats=args.ats,
                limit=args.limit,
            )
        if args.hunt_command == "today":
            return hunt.run_today(
                role_families=[] if args.all_roles else list(DEFAULT_TECHNICAL_FAMILIES),
                ats=args.ats,
            )
        if args.hunt_command == "sync":
            return hunt.run_sync()
        if args.hunt_command == "open":
            return hunt.run_open_listing(args.ref, source=args.source)
        if args.hunt_command == "apps":
            return hunt.run_apps(args.ref, args.status)
        if args.hunt_command == "skipped":
            if args.action == "restore" and not args.ref:
                raise RuntimeError("'skipped restore' needs a row from the skipped table")
            return hunt.run_skipped(args.ref if args.action == "restore" else None)
        if args.hunt_command == "inbox":
            return inbox.run_inbox()
        if args.hunt_command == "patterns":
            return knowledge.run_patterns(every=args.every, check=args.check)
        if args.hunt_command == "prose":
            return knowledge.run_prose(
                args.action, args.ref, question=args.question, file=args.file
            )
        if args.hunt_command == "archive":
            return knowledge.run_archive(args.section, markdown=args.markdown)
        if args.hunt_command == "facts":
            if args.fact_command == "list":
                return facts.run_facts_list()
            if args.fact_command == "review":
                return facts.run_facts_review()
            if args.fact_command == "delete":
                return facts.run_facts_delete(args.key, yes=args.yes)
            if args.fact_command == "set":
                if (args.value is None) != args.none:
                    raise RuntimeError("'facts set' takes a value or --none, not both")
                return facts.run_facts_set(
                    args.key,
                    args.value,
                    value_type=args.value_type,
                    label=args.label,
                )
        if args.hunt_command == "resume":
            return run_resume(args)
        if args.hunt_command == "repair":
            return repairs.run_repair(watch=args.watch, until=args.until)
        if args.hunt_command == "validate":
            return repairs.run_validate()
        if args.hunt_command == "replay":
            return knowledge.run_replay(
                question=args.question, fact_key=args.fact_key, json_line=args.json_line
            )
        if args.hunt_command == "changes":
            return history.run_changes(
                args.action, args.ref, subject=args.subject, status=args.change_status
            )
        if args.hunt_command == "fixes":
            if args.retry:
                return hunt.run_autofill_failure_command("retry", args.retry)
            return history.run_fixes()
        if args.hunt_command == "sites":
            return history.run_sites()
        if args.hunt_command == "apply":
            if args.ref == "failures":
                return hunt.run_autofill_failure_command(args.failure_action, args.failure_ref)
            if args.failure_action or args.failure_ref:
                raise RuntimeError("Unexpected autofill arguments")
            if args.done:
                if (args.dry_run or args.no_claude or args.ats or not args.repair
                        or args.fresh or args.explain or args.step or args.ask_facts
                        or args.remote):
                    raise RuntimeError("--done takes no other flags")
                if args.ref is None or args.ref == "today":
                    raise RuntimeError("--done needs a listing row or id")
                return hunt.run_mark_applied(args.ref)
            if args.remote and args.step:
                raise RuntimeError("--step pauses at a terminal; a remote run has none")
            if args.ref is None or args.ref == "today":
                return hunt.run_autofill_top(
                    today=args.ref == "today",
                    ats=args.ats,
                    dry_run=args.dry_run,
                    no_claude=args.no_claude,
                    repair=args.repair,
                    fresh=args.fresh,
                    explain=args.explain,
                    step=args.step,
                    ask_facts=args.ask_facts,
                    remote=args.remote,
                    accept_letter=args.accept_letter,
                )
            if args.ats:
                raise RuntimeError("--ats picks the top row; do not give a row with it")
            return hunt.run_autofill(
                args.ref,
                dry_run=args.dry_run,
                no_claude=args.no_claude,
                repair=args.repair,
                fresh=args.fresh,
                explain=args.explain,
                step=args.step,
                ask_facts=args.ask_facts,
                referred=args.referred,
                company=args.company,
                title=args.title,
                remote=args.remote,
                accept_letter=args.accept_letter,
            )
    return 2


def expand_shorthand(arguments: list[str]) -> list[str]:
    """The short spellings of the everyday commands.

    `hunt` is `hunt list`, `hunt 20` is `hunt list 20`, and `hunt facts` is
    `hunt facts list`.
    """
    if not arguments or arguments[0] != "hunt":
        return arguments
    rest = arguments[1:]
    if not rest or rest[0].isdigit() or rest[0].split("=", 1)[0] in LISTING_FLAGS:
        return ["hunt", "list", *rest]
    if rest == ["facts"]:
        return ["hunt", "facts", "list"]
    if rest[0] not in (*HUNT_COMMANDS, "list") and not rest[0].startswith("-"):
        raise SystemExit(
            f"os hunt: unknown command {rest[0]!r}. Use a number of rows, or one of: "
            + ", ".join(HUNT_COMMANDS)
        )
    return arguments


def main(arguments: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if arguments is None else arguments)
    args = build_parser().parse_args(expand_shorthand(argv))
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
