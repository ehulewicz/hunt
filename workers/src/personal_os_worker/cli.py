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

from personal_os_worker.cli_parser import build_parser, expand_shorthand
from personal_os_worker.core import commands as services
from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.auth import LoginError
from personal_os_worker.core.config import load_cli_config
from personal_os_worker.core.service import ApiUnavailable, ChromeUnavailable
from personal_os_worker.core.tables import ReferenceError, console
from personal_os_worker.core.tls import tls
from personal_os_worker.device import commands as devices
from personal_os_worker.documents import resume as resumes
from personal_os_worker.jobs import apply_commands, history, inbox, knowledge
from personal_os_worker.jobs import commands as hunt
from personal_os_worker.jobs.commands import DEFAULT_TECHNICAL_FAMILIES
from personal_os_worker.jobs.facts import commands as facts
from personal_os_worker.jobs.repair import commands as repairs


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
        return _dispatch_hunt(args)
    return 2


def _dispatch_hunt(args: argparse.Namespace) -> int:
    """Run one `os hunt` command through the dispatcher for its group."""
    if args.hunt_command in ("list", "today", "sync", "open", "apps", "skipped"):
        return _dispatch_listings(args)
    if args.hunt_command in ("inbox", "patterns", "library", "prose", "archive"):
        return _dispatch_learning(args)
    if args.hunt_command == "facts":
        return _dispatch_facts(args)
    if args.hunt_command == "resume":
        return run_resume(args)
    if args.hunt_command in ("repair", "validate", "replay"):
        return _dispatch_repair(args)
    if args.hunt_command in ("changes", "fixes", "sites"):
        return _dispatch_history(args)
    if args.hunt_command == "apply":
        return _dispatch_apply(args)
    return 2


def _dispatch_listings(args: argparse.Namespace) -> int:
    """Run a board command: list, today, sync, open, apps or skipped."""
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
    return 2


def _dispatch_learning(args: argparse.Namespace) -> int:
    """Run inbox, patterns, library, prose or archive."""
    if args.hunt_command == "inbox":
        return inbox.run_inbox()
    if args.hunt_command == "patterns":
        return knowledge.run_patterns(every=args.every, check=args.check)
    if args.hunt_command == "library":
        from personal_os_worker.core import commands as _services
        from personal_os_worker.jobs import library as library_cmd
        config, token = _services.connected()
        api = WorkerApi(config, token)
        try:
            if args.library_command == "get":
                return library_cmd.run_library_get(api)
            if args.library_command == "put":
                return library_cmd.run_library_put(api, args.file)
            return library_cmd.run_library_import(api, args.file, dry_run=args.dry_run)
        finally:
            api.close()
    if args.hunt_command == "prose" and args.action in ("try", "coverage"):
        from personal_os_worker.jobs import prose_tools
        config, token = services.connected()
        api = WorkerApi(config, token)
        try:
            if args.action == "try":
                return prose_tools.run_prose_try(
                    api, library=Path(args.library), questions=Path(args.questions),
                    out=Path(args.out), max_calls=args.max_calls,
                )
            return prose_tools.run_prose_coverage(
                api, scan=args.scan, max_calls=args.max_calls,
            )
        finally:
            api.close()
    if args.hunt_command == "prose":
        return knowledge.run_prose(
            args.action, args.ref, question=args.question, file=args.file
        )
    if args.hunt_command == "archive":
        return knowledge.run_archive(args.section, markdown=args.markdown)
    return 2


def _dispatch_facts(args: argparse.Namespace) -> int:
    """Run one `os hunt facts` command."""
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
    return 2


def _dispatch_repair(args: argparse.Namespace) -> int:
    """Run repair, validate or replay."""
    if args.hunt_command == "repair":
        return repairs.run_repair(watch=args.watch, until=args.until)
    if args.hunt_command == "validate":
        return repairs.run_validate()
    if args.hunt_command == "replay":
        return knowledge.run_replay(
            question=args.question, fact_key=args.fact_key, json_line=args.json_line
        )
    return 2


def _dispatch_history(args: argparse.Namespace) -> int:
    """Run changes, fixes or sites."""
    if args.hunt_command == "changes":
        return history.run_changes(
            args.action, args.ref, subject=args.subject, status=args.change_status
        )
    if args.hunt_command == "fixes":
        if args.retry:
            return apply_commands.run_autofill_failure_command("retry", args.retry)
        return history.run_fixes()
    if args.hunt_command == "sites":
        return history.run_sites()
    return 2


def _dispatch_apply(args: argparse.Namespace) -> int:
    """Run `os hunt apply`: fill a listing, record one made elsewhere, or list failures."""
    if args.ref == "failures":
        return apply_commands.run_autofill_failure_command(args.failure_action, args.failure_ref)
    if args.failure_action or args.failure_ref:
        raise RuntimeError("Unexpected autofill arguments")
    if args.done:
        if (args.dry_run or args.no_claude or args.ats or not args.repair
                or args.fresh or args.explain or args.step or args.ask_facts
                or args.remote or args.accept_letter or args.referred is not None):
            raise RuntimeError("--done takes no other flags but --company and --title")
        if args.ref is None or args.ref == "today":
            raise RuntimeError("--done needs a listing row or id")
        return apply_commands.run_mark_applied(args.ref, company=args.company, title=args.title)
    if args.remote and args.step:
        raise RuntimeError("--step pauses at a terminal; a remote run has none")
    if args.ref is None or args.ref == "today":
        return apply_commands.run_autofill_top(
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
    return apply_commands.run_autofill(
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
