"""The `os` parser: its commands, flags, defaults and help text, and the short spellings."""

from __future__ import annotations

import argparse

from personal_os_worker import __version__
from personal_os_worker.jobs import history
from personal_os_worker.jobs.commands import APPLICATION_STATUSES, DEFAULT_TECHNICAL_FAMILIES
from personal_os_worker.jobs.facts.commands import VALUE_TYPES
from personal_os_worker.jobs.sites import ATS_NAMES

HUNT_COMMANDS = (
    "today", "sync", "open", "apps", "skipped", "inbox", "patterns", "facts", "prose",
    "library",
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

    _add_hunt_parser(commands)
    return parser


def _add_hunt_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """`os hunt` and its subcommands, one group at a time."""
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
    _add_listing_commands(jobs_commands)
    _add_learning_commands(jobs_commands)
    _add_facts_command(jobs_commands)
    _add_resume_command(jobs_commands)
    _add_apply_command(jobs_commands)
    _add_repair_commands(jobs_commands)
    _add_history_commands(jobs_commands)


def _add_listing_commands(
    jobs_commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The board's commands: list, today, sync and open, then skipped and apps."""
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


def _add_learning_commands(
    jobs_commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """What was learned and the writing: inbox, patterns, prose, library and archive."""
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
    prose.add_argument("action", nargs="?",
                       choices=["list", "delete", "try", "coverage"])
    prose.add_argument("ref", nargs="?", help="Row number from 'os hunt prose' or id prefix")
    prose.add_argument("--library", help="Question-bank markdown file (try)")
    prose.add_argument("--questions", help="Audit questions TSV (try)")
    prose.add_argument("--out", help="Report markdown path (try)")
    prose.add_argument("--scan", action="store_true", help="Scan stored prose questions (coverage)")
    prose.add_argument("--max-calls", type=int, help="Bound this batch's model calls")
    library = jobs_commands.add_parser("library", help="The narrative library")
    library_actions = library.add_subparsers(dest="library_command", required=True)
    library_actions.add_parser("get", help="Print the library's entries as JSON to stdout")
    library_actions.add_parser(
        "list", help="Each entry with its word count and human share"
    )
    library_score = library_actions.add_parser(
        "score", help="Set an entry's GPTZero human share"
    )
    library_score.add_argument("code", help="The entry's code")
    library_score.add_argument(
        "percent", type=int, help="GPTZero's human share, 0 to 100"
    )
    library_put = library_actions.add_parser("put", help="Save library JSON (one entry or a list)")
    library_put.add_argument("file", help="The library JSON file to save")
    library_import = library_actions.add_parser(
        "import", help="Load a question-bank markdown file into the empty library, once"
    )
    library_import.add_argument("file", help="The bank markdown file")
    library_import.add_argument(
        "--dry-run", action="store_true", help="Parse and link only; import nothing"
    )
    archive = jobs_commands.add_parser(
        "archive", help="The old system, frozen: answers, retired rules, long-form writing"
    )
    archive.add_argument("section", nargs="?", default="answers",
                         choices=["answers", "rules", "prose"])
    archive.add_argument("--markdown", help="Write archived long-form answers to this file")


def _add_facts_command(
    jobs_commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """`os hunt facts` and its list, review, delete and set."""
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


def _add_resume_command(
    jobs_commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """`os hunt resume` and its get and put."""
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


def _add_apply_command(
    jobs_commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """`os hunt apply`: what to apply to, then its flags."""
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
    _add_apply_flags(autofill)


def _add_apply_flags(autofill: argparse.ArgumentParser) -> None:
    """Every `os hunt apply` flag, from --dry-run to --remote, in help order."""
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


def _add_repair_commands(
    jobs_commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Repair, validation and replay of what the worker changed."""
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


def _add_history_commands(
    jobs_commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The record of changes, fixes and each site's latest run."""
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
