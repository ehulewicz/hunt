"""`os hunt apply`: pick the listing, run it, record one made elsewhere, list failures."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.commands import connected
from personal_os_worker.core.service import ensure_chrome
from personal_os_worker.core.tables import console, pick
from personal_os_worker.jobs.apply_run import apply_posting
from personal_os_worker.jobs.commands import (
    DEFAULT_TECHNICAL_FAMILIES,
    by_ats,
    today_listings,
)

POSTING_URL = re.compile(r"^https?://", re.IGNORECASE)
SITE_SUFFIX = re.compile(r"\.(jobs|careers|com|io|co|net|org)$", re.IGNORECASE)
# Title segments that name the page, not the role or the employer.
GENERIC_SEGMENT = re.compile(
    r"^(careers?|career page|career site|jobs?|job board|job application|apply|open roles?"
    r"|greenhouse|lever|workday|ashby|icims|smartrecruiters|jobvite|jazzhr|bamboohr)$",
    re.IGNORECASE,
)
ROLE_WORDS = re.compile(
    r"\b(intern|internship|co-?op|engineer|developer|scientist|analyst|researcher|associate"
    r"|specialist|designer|manager|software|technician)\b",
    re.IGNORECASE,
)
# Hosted boards put the employer in the subdomain or the first path segment.
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "applytojob.com", "icims.com",
             "smartrecruiters.com", "myworkdayjobs.com", "jobvite.com", "bamboohr.com")


def _company_from_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    segments = [segment for segment in parts.path.split("/") if segment]
    if any(host.endswith(ats) for ats in ATS_HOSTS):
        label = host.split(".")[0]
        if label in {"boards", "job-boards", "jobs", "www", "apply", "careers"} and segments:
            return segments[0].replace("-", " ")
        return label.split("-")[-1] if label.startswith("careers-") else label
    labels = host.split(".")
    return labels[-2] if len(labels) >= 2 else labels[0]


def guess_identity(
    *, page_title: str, og_title: str, site_name: str, url: str, heading: str = ""
) -> tuple[str, str]:
    """(company, title) read off a posting page: its title, heading, site name, URL."""
    title_text = " ".join((page_title or og_title or "").split())
    company = " ".join((site_name or "").split())
    if GENERIC_SEGMENT.match(company):
        company = ""
    role = ""
    at = re.match(
        r"^(?:job application for\s+)?(.+?)\s+(?:at|@)\s+(.+)$", title_text, re.IGNORECASE
    )
    if at:
        role, company = at.group(1), company or at.group(2)
    else:
        segments = [
            re.sub(r"\s+(careers?|jobs)$", "", part, flags=re.IGNORECASE).strip()
            for part in re.split(r"\s+[|\u2013\u2014-]\s+|\s+\|\s*", title_text)
        ]
        segments = [part for part in segments if part and not GENERIC_SEGMENT.match(part)]
        roles = [part for part in segments if ROLE_WORDS.search(part)]
        role = roles[0] if roles else (segments[0] if segments else "")
        # The employer is the last other segment that is not an id ("Job ID: 1").
        others = [
            part for part in segments
            if part != role and not re.search(r"\b(job id|req(uisition)?|id)\b|^#?\d", part, re.I)
        ]
        company = company or (others[-1] if others else "")
    if not ROLE_WORDS.search(role):
        for candidate in (heading, og_title):
            if candidate and ROLE_WORDS.search(candidate):
                role = candidate
                break
    company = SITE_SUFFIX.sub("", company or _company_from_url(url)).strip()
    return company[:1].upper() + company[1:], " ".join(role.split())


def read_posting_identity(url: str) -> tuple[str, str]:
    """Open the URL in the automation browser and read who is hiring for what."""
    from personal_os_worker.jobs.browser import VisibleBrowser, rendered_body_text

    config, _token = connected()
    ensure_chrome(config.cdp_url)
    with VisibleBrowser(config.cdp_url) as browser:
        page, _reused = browser.application_page(url, console_errors=[], fresh=False)
        rendered_body_text(page, timeout_ms=15_000)
        meta = page.evaluate(
            """() => ({
              page_title: document.title || '',
              og_title: document.querySelector('meta[property="og:title"]')?.content || '',
              site_name: document.querySelector('meta[property="og:site_name"]')?.content || '',
              heading: document.querySelector('h1')?.innerText || '',
            })"""
        )
    return guess_identity(url=url, **{key: str(value) for key, value in meta.items()})


def listing_for(
    api: WorkerApi, reference: str, *, company: str | None = None, title: str | None = None
) -> dict[str, Any]:
    """A board row, an id prefix, or any posting URL (a referral link included)."""
    if not POSTING_URL.match(reference):
        return api.listing(reference)
    listing = api.listing_from_url(reference)
    if listing is None:
        if not (company and title):
            read_company, read_title = read_posting_identity(reference)
            company, title = company or read_company, title or read_title
        console.print(f"New posting from its URL: [bold]{company}[/bold] \u2014 {title}")
        listing = api.listing_from_url(reference, company=company, title=title)
    if listing is None:
        raise RuntimeError("Could not record a posting for that URL")
    return listing


def run_autofill(
    reference: str,
    *,
    dry_run: bool,
    no_claude: bool = False,
    repair: bool = True,
    fresh: bool = False,
    explain: bool = False,
    step: bool = False,
    ask_facts: bool = False,
    referred: str | None = None,
    company: str | None = None,
    title: str | None = None,
    remote: bool = False,
    accept_letter: bool = False,
) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        listing = listing_for(api, reference, company=company, title=title)
    finally:
        api.close()
    apply_posting(
        str(listing["id"]), dry_run=dry_run, no_claude=no_claude, repair=repair,
        fresh=fresh, explain=explain, step=step, ask_facts=ask_facts, referred=referred,
        remote=remote, accept_letter=accept_letter,
    )
    return 0


def run_autofill_top(
    *,
    today: bool,
    ats: str | None,
    dry_run: bool,
    no_claude: bool = False,
    repair: bool = True,
    fresh: bool = False,
    explain: bool = False,
    step: bool = False,
    ask_facts: bool = False,
    remote: bool = False,
    accept_letter: bool = False,
) -> int:
    """Apply the first board row (or first of today), on the given ATS when one is named."""
    families = list(DEFAULT_TECHNICAL_FAMILIES)
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        if today:
            items = today_listings(api, role_families=families, ats=ats)
        else:
            items = list(api.listings(role_families=families, limit=1000).get("items") or [])
            if ats:
                items = by_ats(items, ats)
    finally:
        api.close()
    if not items:
        where = f"on {ats}" if ats else "on the board"
        raise RuntimeError(f"Nothing {'new today' if today else 'listed'} {where}.")
    listing = items[0]
    console.print(
        f"Applying row {listing.get('row')}: {listing.get('company_name')} — {listing.get('title')}"
    )
    apply_posting(
        str(listing["id"]), dry_run=dry_run, no_claude=no_claude, repair=repair,
        fresh=fresh, explain=explain, step=step, ask_facts=ask_facts, remote=remote,
        accept_letter=accept_letter,
    )
    return 0


def run_mark_applied(reference: str) -> int:
    """`apply <row> --done`: you applied outside the worker; record it as applied."""
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        listing = listing_for(api, reference)
        posting_id = str(listing["id"])
        application = next(
            (item for item in api.applications() if str(item.get("posting_id")) == posting_id),
            None,
        )
        if application is None:
            updated = api.create_application(posting_id, status="applied")
        else:
            updated = api.update_application(str(application["id"]), status="applied")
    finally:
        api.close()
    console.print(f"Marked {updated.get('company_name')} — {updated.get('title')} as applied.")
    return 0


def run_autofill_failure_command(action: str | None, reference: str | None) -> int:
    """List what runs could not settle, show one, or retry an unsolved problem.

    A fixer's problem is one failure-issue row (its signature carries
    "problem|"); an unsolved one stays unsolved until `--retry` puts it back
    on the queue or a new fixer API version reopens them all.
    """
    from personal_os_worker.core.tables import short_id

    if action not in {"list", "show", "retry"}:
        raise RuntimeError("Use: os hunt fixes [list|show|--retry]")
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        issues = api.autofill_failures()
        if action == "list":
            if not issues:
                console.print("No autofill failures recorded.")
                return 0
            for index, issue in enumerate(issues, 1):
                notes = str(issue.get("verification_notes") or "")
                attempts = notes.split()[1] if notes.startswith("attempts ") else ""
                problem = "problem " if "problem|" in str(issue.get("signature") or "") else ""
                console.print(
                    f"{index:>3} {short_id(str(issue.get('id')))} "
                    f"{issue.get('status')} {issue.get('ats')} {issue.get('category')} "
                    f"{problem}{issue.get('field_label') or ''} "
                    f"x{issue.get('occurrence_count')}"
                    + (f" ({attempts})" if attempts else "")
                )
            return 0
        if action == "retry":
            if reference is None:
                raise RuntimeError("'--retry' requires a failure row or id prefix")
            issue = pick(issues, reference, kind="failures")
            issue_id = str(issue["id"])
            if str(issue.get("status")) != "unsolved":
                raise RuntimeError(
                    f"failure {short_id(issue_id)} is {issue.get('status')}, "
                    "only an unsolved one goes back on the queue"
                )
            api.update_autofill_failure(
                issue_id,
                {"status": "open",
                 "verification_notes": "attempts 0 of 3; reopened by --retry"},
            )
            console.print(f"failure {short_id(issue_id)} is back on the queue.")
            return 0
        if reference is None:
            raise RuntimeError("'show' requires a failure row or id prefix")
        issue_id = str(pick(issues, reference, kind="failures")["id"])
        issue = api.autofill_failure(issue_id)
        import json

        console.print_json(json.dumps(issue, default=str))
        return 0
    finally:
        api.close()
