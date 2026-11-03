"""One `os hunt apply` run: create it, reach the posting, fill it, and record how it ended."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Error

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.commands import connected
from personal_os_worker.core.config import console
from personal_os_worker.core.doctor import python_check
from personal_os_worker.core.service import ensure_chrome
from personal_os_worker.device.link import socket_path
from personal_os_worker.jobs.account import AccountFlow
from personal_os_worker.jobs.compose import LibraryText, library_texts
from personal_os_worker.jobs.posting_page import _reach_the_posting
from personal_os_worker.jobs.resolve import Knowledge

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from personal_os_worker.core.config import CliConfig
    from personal_os_worker.jobs.browser import VisibleBrowser
    from personal_os_worker.jobs.fix.queue import FixQueue
    from personal_os_worker.jobs.remote import RunLink


@dataclass(frozen=True, slots=True)
class _ApplyOptions:
    """The flags one apply run was started with."""

    dry_run: bool
    no_claude: bool
    repair: bool
    fresh: bool
    explain: bool
    step: bool
    ask_facts: bool
    referred: str | None
    remote: bool
    accept_letter: bool


def apply_posting(
    posting_id: str,
    *,
    dry_run: bool,
    no_claude: bool,
    repair: bool = True,
    fresh: bool = False,
    explain: bool = False,
    step: bool = False,
    ask_facts: bool = False,
    referred: str | None = None,
    remote: bool = False,
    accept_letter: bool = False,
) -> str:
    """Run one interactive apply session: "submitted", "skipped", or "stopped".

    `accept_letter` keeps the filled cover letter without a review; remote runs
    never pass it.

    `referred` is None unless this application came through a referral: then it
    is the referrer's name, or "" when none was given. With this device's agent
    listening, the run registers with it so the Hunt page sees and drives it;
    `remote` is a run the page started, whose letter and facts the page answers.
    """
    return _run_application(
        posting_id,
        _ApplyOptions(
            dry_run=dry_run, no_claude=no_claude, repair=repair, fresh=fresh,
            explain=explain, step=step, ask_facts=ask_facts, referred=referred,
            remote=remote, accept_letter=accept_letter,
        ),
    )


def _run_application(posting_id: str, options: _ApplyOptions) -> str:
    """Create the run and fill it, closing what it opened on every path."""
    from personal_os_worker.jobs.browser import VisibleBrowser
    from personal_os_worker.jobs.remote import RunLink, mirrored

    config, api = _prepare(options.remote)
    run_id: str | None = None
    link: RunLink | None = None
    fixes: FixQueue | None = None
    mirror = contextlib.ExitStack()
    result = "failed"
    try:
        career, resume_json, resume_pdf, run, library = _create_run(api, posting_id)
        run_id = str(run["run_id"])
        link = RunLink.connect(
            run_id=run_id,
            company=str(run["company_name"]),
            title=str(run["title"]),
            remote=options.remote,
        )
        if link is not None:
            mirror.enter_context(mirrored(link))
        elif options.remote:
            raise RuntimeError("This device's agent did not take the run")
        direct_url = str(run["application_url"])
        # A label for querying the fill events; every site is filled the same way.
        ats = str(run.get("ats") or "generic")
        knowledge = _knowledge(
            run, career=career, run_id=run_id, direct_url=direct_url, referred=options.referred
        )
        _print_notices(run, knowledge, options.referred)
        with VisibleBrowser(config.cdp_url) as browser:
            console_errors: list[str] = []
            page, reused = browser.application_page(
                direct_url, console_errors=console_errors, fresh=options.fresh
            )
            if link is not None:
                link.tab(page)
            if not options.no_claude and not options.dry_run:
                fixes = _fix_queue(api, run_id, link)
            result = _fill_application(
                api, browser, page, console_errors, reused=reused, run=run, run_id=run_id,
                ats=ats, knowledge=knowledge, career=career, resume_json=resume_json,
                resume_pdf=resume_pdf, library=library, fixes=fixes, link=link,
                cdp_url=config.cdp_url, options=options,
            )
            return result
    except KeyboardInterrupt:
        result = "stopped"
        if run_id is not None:
            api.stop_autofill_run(run_id, status="abandoned")
        raise
    except Exception as error:
        if link is not None:
            # The terminal hears this from the CLI; the page only through the link.
            link.log([{"text": str(error), "style": ["red"]}])
        if run_id is not None:
            api.stop_autofill_run(run_id, status="failed")
        raise
    finally:
        if fixes is not None:
            fixes.close()
        mirror.close()
        if link is not None:
            link.end(result)
            link.close()
        api.close()
    return "stopped"


def _prepare(remote: bool) -> tuple[CliConfig, WorkerApi]:
    """Refuse a run that cannot start, then connect and settle what earlier runs left."""
    from personal_os_worker.core.service import state_dir
    from personal_os_worker.jobs.fix.gates import clear_stale
    from personal_os_worker.jobs.knowledge import report_pending_facts, resolve_pending_facts

    if remote and not socket_path().exists():
        raise RuntimeError("--remote is for runs this device's agent starts; none is listening")
    # A run on the wrong interpreter fills as usual while its memory climbs
    # until the machine runs out, so it never starts.
    python = python_check()
    if not python.ok:
        raise RuntimeError(f"{python.name}: {python.detail}")
    config, token = connected()
    ensure_chrome(config.cdp_url)
    api = WorkerApi(config, token)
    try:
        # A fact you set on the web since the last run is checked before this one.
        report_pending_facts(resolve_pending_facts(api))
        # Attempt folders from a run that never got to tidy up are gone by now.
        clear_stale(state_dir() / "fix")
    except BaseException:
        api.close()
        raise
    return config, api


def _create_run(
    api: WorkerApi, posting_id: str
) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any], list[LibraryText]]:
    """Read the career record, the library, and the resume PDF, then create the run."""
    from personal_os_worker.core.service import state_dir
    from personal_os_worker.jobs.facts.career import download_resume_pdf
    from personal_os_worker.jobs.sites import adapter_version

    career = api.career()
    resume_json = dict(career["document"])
    library = library_texts(api.library(), resume_json)
    resume_pdf = download_resume_pdf(api, state_dir() / "resume")
    version = adapter_version()
    run = api.create_autofill_run(
            posting_id=posting_id, ats="generic", adapter_version=version
        )
    return career, resume_json, resume_pdf, run, library


def _knowledge(
    run: dict[str, Any],
    *,
    career: dict[str, Any],
    run_id: str,
    direct_url: str,
    referred: str | None,
) -> Knowledge:
    """What is known for this application, built from the run and the career record."""
    from personal_os_worker.jobs.browser import registrable_site

    knowledge = Knowledge.build(
        facts=list(run.get("facts") or []),
        career=career,
        patterns=list(run.get("patterns") or []),
        prose=list(run.get("prose") or []),
        company=str(run["company_name"]),
        # Random choices (office selection) are seeded per application (design §4).
        seed=str(run.get("application_id") or run_id),
        today=date.today(),
        preferences=list(run.get("control_preferences") or []),
        prior_applications=list(run.get("duplicate_company_applications") or []),
        title=str(run["title"]),
        referral={"referred": referred is not None, "name": referred or None},
        site=registrable_site(direct_url),
    )
    return knowledge


def _print_notices(run: dict[str, Any], knowledge: Knowledge, referred: str | None) -> None:
    """Warn about a clash with school or other applications here, and note a referral."""
    if knowledge.facts.get("availability.conflicts_with_school"):
        console.print(
            f"[yellow]Warning:[/yellow] this posting's dates "
            f"({knowledge.facts.get('availability.internship_dates')}) run into your last "
            "semester; you meant to avoid Fall 2027."
        )
    if referred is not None:
        console.print(
            "  [dim]referred" + (f" by {referred}" if referred else "") + "[/dim]"
        )
    duplicates = list(run.get("duplicate_company_applications") or [])
    if duplicates:
        console.print(
            f"[yellow]Warning:[/yellow] {len(duplicates)} other application(s) at "
            f"{run['company_name']} already exist."
        )


def _fix_queue(api: WorkerApi, run_id: str, link: RunLink | None) -> FixQueue:
    """Reopen problems a new fixer can try, and start this run's fix queue."""
    from personal_os_worker.core.service import state_dir
    from personal_os_worker.jobs import registry
    from personal_os_worker.jobs.fix.queue import FixQueue

    reopened = registry.reopen_for_version(state_dir(), api)
    if reopened:
        console.print(
            f"  [dim]reopened {reopened} unsolved fix problem(s) "
            "for the new fixer[/dim]"
        )
    return FixQueue(
        repo=Path(__file__).resolve().parents[4],
        workdir=state_dir() / "fix",
        api=api,
        run_id=run_id,
        report=lambda line: console.print(f"  [dim]{line}[/dim]"),
        publish=link.fixes if link is not None else None,
        loader=lambda path, category: registry.load_file(
            path, category=category
        ),
    )


def _fill_application(
    api: WorkerApi,
    browser: VisibleBrowser,
    page: Page,
    console_errors: list[str],
    *,
    reused: bool,
    run: dict[str, Any],
    run_id: str,
    ats: str,
    knowledge: Knowledge,
    career: dict[str, Any],
    resume_json: dict[str, Any],
    resume_pdf: Path,
    library: list[LibraryText],
    fixes: FixQueue | None,
    link: RunLink | None,
    cdp_url: str,
    options: _ApplyOptions,
) -> str:
    """Reach the posting, fill it in one session, and record how the run ended."""
    from personal_os_worker.jobs.facts.career import transcript_pdf_path
    from personal_os_worker.jobs.session import AutofillSession, SessionContext

    expected_company = str(run["company_name"])
    expected_title = str(run["title"])
    page, body = _reach_the_posting(
        api, browser, page, console_errors, run_id=run_id, ats=ats,
        expected_company=expected_company, expected_title=expected_title, fixes=fixes,
        link=link, cdp_url=cdp_url,
    )
    _announce(expected_company, expected_title, ats, reused, resume_pdf, options)
    linked = _linked(link, options.accept_letter)
    session = AutofillSession(
        SessionContext(
            api=api,
            run_id=run_id,
            ats=ats,
            browser=browser,
            page=page,
            console_errors=console_errors,
            knowledge=knowledge,
            resume_json=resume_json,
            resume_pdf=resume_pdf,
            company=expected_company,
            title=expected_title,
            description=body,
            career=career,
            library=tuple(library),
            transcript_pdf=transcript_pdf_path(),
            dry_run=options.dry_run,
            use_claude=not options.no_claude,
            repair=options.repair,
            explain=options.explain,
            step=options.step,
            # A remote run has no terminal to ask at; the page asks instead.
            ask_facts=options.ask_facts and not options.remote,
            account=account_flow(knowledge, expected_company),
            fixes=fixes,
            cdp_url=cdp_url,
            fact_ranker=api.rank_facts,
            **linked,
        )
    )
    outcome = session.run()
    result = finalize_application_outcome(
        api,
        run=run,
        run_id=run_id,
        outcome=outcome,
        page=page,
    )
    return result


def _announce(
    expected_company: str,
    expected_title: str,
    ats: str,
    reused: bool,
    resume_pdf: Path,
    options: _ApplyOptions,
) -> None:
    """Print which posting this run fills, and how."""
    console.print(
        f"[bold]{expected_company}[/bold] — {expected_title}  [dim]({ats})[/dim]"
    )
    if reused:
        console.print(
            "  [dim]continuing on the tab already open for this application; "
            "--fresh opens a new one[/dim]"
        )
    if options.repair and not options.dry_run:
        console.print(
            "  [dim]repair on: a field that fails is retried here, on this page[/dim]"
        )
    console.print(f"  [dim]resume  {resume_pdf}[/dim]")


def _linked(link: RunLink | None, accept_letter: bool) -> dict[str, Any]:
    """The session's link, keys and letter editor, as this run is linked or not."""
    from personal_os_worker.jobs.cover_letter import review_letter
    from personal_os_worker.jobs.keys import KeyWatcher
    from personal_os_worker.jobs.remote import LinkedKeys

    linked: dict[str, Any] = {}
    if link is not None:
        linked = {"link": link, "keys": LinkedKeys(link, KeyWatcher())}
    # The letter review goes to the Hunt page when a run is linked, and
    # leaves the letter for the page otherwise.
    linked["editor"] = partial(review_letter, link=link, accept=accept_letter)
    return linked


def account_flow(knowledge: Knowledge, company: str) -> AccountFlow | None:
    """The account-wall flow, signing in as the contact.email fact; None without one."""
    email = str(knowledge.facts.get("contact.email") or "").strip()
    return AccountFlow(email=email, company=company) if email else None


def finalize_application_outcome(
    api: WorkerApi,
    *,
    run: dict[str, Any],
    run_id: str,
    outcome: dict[str, Any],
    page: Any,
) -> str:
    """Persist a clean terminal outcome, then close only its application page."""
    if outcome.get("dry_run"):
        api.stop_autofill_run(run_id, status="abandoned")
        console.print("Dry run: no values were changed and nothing was submitted.")
        return "stopped"

    saved = list(outcome["answers"])
    company = str(run["company_name"])
    if outcome.get("submitted"):
        application_status, result = "applied", "submitted"
    elif outcome.get("skipped"):
        application_status, result = "skipped", "skipped"
    else:
        application_status, result = "archived", "stopped"
    api.complete_autofill_run(
        run_id,
        final_url=str(outcome.get("final_url") or ""),
        answers=saved,
        submitted=bool(outcome.get("submitted")),
        application_status=application_status,
    )
    try:
        if not page.is_closed():
            page.close()
    except Error:
        # An already-disconnected Page has no cleanup work left to do.
        pass

    verb = "Stopped" if outcome.get("interrupted") else "Done"
    console.print(f"{verb}. Recorded {len(saved)} answer(s) for this application.")
    if result == "submitted":
        console.print(f"Marked {company} as applied.")
    elif result == "skipped":
        console.print(f"Skipped {company}; 'os hunt skipped restore' undoes it.")
    return result
