"""The posting page an apply run reaches: read it, refuse a closed one, find it."""

from __future__ import annotations

import re
from functools import partial
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import Page

    from personal_os_worker.core.api import WorkerApi
    from personal_os_worker.jobs.browser import VisibleBrowser
    from personal_os_worker.jobs.fix.queue import FixQueue
    from personal_os_worker.jobs.remote import RunLink

CLOSED_POSTING = re.compile(
    r"job is closed|no longer accepting|position has been filled|job (you requested )?(was )?"
    r"not found|(job|position|posting|role|opening)"
    r"( (that )?you(['\u2019]re| are)? (looking for|requested|searched for))?"
    r" is no longer (available|open|active)"
    r"|this job (has )?expired|no longer (available|open) for applications"
    r"|page you are looking for (doesn['\u2019]t|does not) exist",
    re.IGNORECASE,
)


class PostingClosed(RuntimeError):
    """The posting is gone: the site says so, or its search shows it, or you said so."""

    def __init__(self, reason: str, page: Page) -> None:
        super().__init__(reason)
        self.reason = reason
        self.page = page


def _reach_the_posting(
    api: WorkerApi,
    browser: VisibleBrowser,
    page: Page,
    console_errors: list[str],
    *,
    run_id: str,
    ats: str,
    expected_company: str,
    expected_title: str,
    fixes: FixQueue | None,
    link: RunLink | None,
    cdp_url: str,
) -> tuple[Page, str]:
    """Read the posting's text, refuse a closed one, and find it when this page is not it."""
    from personal_os_worker.jobs.browser import (
        identifies_posting,
        inspect_fields,
        looks_like_application_form,
        rendered_body_text,
    )
    from personal_os_worker.jobs.navigate import Target, is_posting, reach_posting

    report_page_failure = partial(
        _report_page_failure,
        api=api,
        run_id=run_id,
        ats=ats,
        page=page,
        console_errors=console_errors,
    )
    # The posting's own text, waited for: it both identifies the page and
    # is the context the cover letter and long-form drafts are written from.
    # A closed notice ends the wait too: a board may say it in a toast that is
    # gone once the text has settled.
    body = rendered_body_text(
        page,
        accept=lambda text: posting_closed(text) or identifies_posting(
            text, company=expected_company, title=expected_title
        ),
    )
    # A closed posting often no longer names the role, so say "closed"
    # before "does not match".
    if posting_closed(body):
        report_page_failure("closed_posting", "The live posting appears closed")
        raise PostingClosed("the site says the posting is no longer open", page)
    # A careers home names the company everywhere; only the role says
    # this is the posting. A form already open with the company on it is.
    on_form = looks_like_application_form(inspect_fields(page))
    if not is_posting(page, expected_title, text=body) and not (
        on_form and expected_company.casefold() in body.casefold()
    ):
        page = reach_posting(
            browser, page, Target(expected_title, expected_company),
            report_page_failure=report_page_failure, arrival_text=body,
            fixes=fixes, run_id=run_id, cdp_url=cdp_url,
        )
        if link is not None:
            link.tab(page)
        body = rendered_body_text(
            page,
            accept=lambda text: identifies_posting(
                text, company=expected_company, title=expected_title
            ),
        )
    return page, body


def _report_page_failure(
    category: str,
    message: str,
    *,
    api: WorkerApi,
    run_id: str,
    ats: str,
    page: Page,
    console_errors: list[str],
    step_number: int = 0,
    offered: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Record a page failure of this run, with the page's evidence attached."""
    from personal_os_worker.jobs.browser import inspect_fields, page_evidence
    from personal_os_worker.jobs.sites import failure_signature

    captured_fields = inspect_fields(page)
    evidence = page_evidence(
        page, captured_fields, console_errors=console_errors
    )
    paths: dict[str, str] = {}
    for name, content in evidence.items():
        content_type = (
            "image/png"
            if name.endswith(".png")
            else "text/html"
            if name.endswith(".html")
            else "application/json"
        )
        paths[name] = api.upload_autofill_artifact(
            run_id,
            name=name,
            content=content,
            content_type=content_type,
        )
    fingerprint = f"{category}|{page.url.split('?', 1)[0]}"
    return api.persist_autofill_step(
        run_id,
        {
            "step_number": step_number,
            "url": page.url,
            "failures": [
                {
                    "signature": failure_signature(ats, category, fingerprint),
                    "category": category,
                    "field_fingerprint": fingerprint,
                    "error_message": message,
                    "artifact_paths": paths,
                    "dom_signals": {"offered": offered} if offered else {},
                }
            ],
        },
    )


def posting_closed(body: str) -> bool:
    """Whether the live page says the posting is gone."""
    return CLOSED_POSTING.search(body) is not None
