"""Finding the posting on its own site, when the URL lands somewhere else.

A listing's application URL is sometimes a careers home or a job list rather
than the posting (Nintendo's "See open roles", then a search). The run reaches
the posting the way you would, deterministically, each step read back:

1. a route written for this site (`jobs/routes/`), when there is one;
2. a link on the page whose text is this role's title;
3. the site's search box, with the full title and then shorter parts of it —
   without the parenthetical, the term, the trailing "Intern" — each time
   looking for a result whose text is the title;
4. a link into the site's open roles ("See open roles", "Search jobs"), then
   the same again, a few hops at most.

The posting is found when the page holds the whole title or a heading is the
title (`is_posting`), never on the company alone: a careers home names the
company everywhere, and a list of results names many roles. Nothing here submits
or signs in, and it never follows a link off the site it started on.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from typing import Any, cast
from urllib.parse import urlsplit

from playwright.sync_api import Error, Page

from personal_os_worker.core.tables import console
from personal_os_worker.jobs.browser import registrable_site, rendered_body_text

MAX_HOPS = 4
RESULT_WAIT_MS = 2_500
# Words that say which term or kind of role a title is for; a site's search
# often matches the role without them.
TERM_WORDS = re.compile(
    r"\b(?:summer|fall|autumn|winter|spring|20\d\d|co-?op|internship|intern)\b", re.IGNORECASE
)
LISTING_TRANSITION = re.compile(
    r"^\s*(?:see|view|browse|search|explore|find|show)\s+(?:all\s+|our\s+)?"
    r"(?:open\s+)?(?:roles|jobs|positions|openings|opportunities|careers)\b"
    r"|^\s*(?:open\s+(?:roles|positions)|job\s+search|search\s+jobs|all\s+jobs|current\s+openings)\s*$",
    re.IGNORECASE,
)
SEARCH_BOX = re.compile(r"search|keyword|job\s*title|find\s+jobs|what", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Target:
    """The posting being looked for."""

    title: str
    company: str


# One spelling for each kind of role, so "Internship" finds "Intern".
KINDS = {"internship": "intern", "interns": "intern", "coop": "co-op"}


def _tokens(text: str) -> list[str]:
    text = re.sub(r"\bco-op\b", "coop", text.casefold())
    return [KINDS.get(token, token) for token in re.findall(r"[a-z0-9+#]+", text)
            if len(token) > 1]


def _head(title: str) -> str:
    """The title without its parenthetical and what runs on past a separator."""
    plain = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", title)
    return re.split(r"\s[-|\u2013\u2014]\s|,\s", plain, maxsplit=1)[0]


def _significant(title: str) -> set[str]:
    """The title's words that say which role it is."""
    stop = {"and", "the", "for", "of", "in", "at", "to", "with", "a", "an"}
    return {token for token in _tokens(title) if token not in stop}


def shows_title(text: str, title: str) -> bool:
    """Whether a text holds the whole title, punctuation aside."""
    wanted = " ".join(_tokens(title))
    return bool(wanted) and wanted in " ".join(_tokens(text))


def title_queries(title: str) -> list[str]:
    """The full title, then shorter parts of it, most specific first, each once."""
    queries: list[str] = []

    def add(query: str) -> None:
        query = " ".join(re.sub("[\u2013\u2014]", " ", query).split()).strip(" -,/|")
        if query and query.casefold() not in {item.casefold() for item in queries}:
            queries.append(query)

    add(title)
    add(re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", title))
    # A title often runs on past a separator: " - Redmond", " | Remote", ", Games".
    head = _head(title)
    add(head)
    add(TERM_WORDS.sub(" ", head))
    words = TERM_WORDS.sub(" ", head).split()
    if len(words) > 2:
        add(" ".join(words[:3]))
        add(" ".join(words[-2:]))
    return queries[:6]


HEADINGS_SCRIPT = r"""
() => [document.title, ...[...document.querySelectorAll('h1, h2, [role="heading"]')]
  .slice(0, 6).map(node => node.innerText || '')]
  .map(text => text.replace(/\s+/g, ' ').trim()).filter(Boolean)
"""


def headings(page: Page) -> list[str]:
    """The document title and the first headings of the page and its frames."""
    found: list[str] = []
    for frame in page.frames:
        try:
            found += cast(list[str], frame.evaluate(HEADINGS_SCRIPT))
        except Error:
            continue
    return found


def is_posting(page: Page, title: str, *, text: str | None = None) -> bool:
    """Whether the page is this role's posting: it holds the whole title, or a
    heading is the title. A careers home or a list of results names the
    company and many roles, but no heading of theirs is this role.
    """
    body = text if text is not None else rendered_body_text(page, timeout_ms=10_000)
    if shows_title(body, title):
        return True
    return any(title_score(heading, title) >= 0.85 for heading in headings(page))


EXACT = 1.5


def title_score(text: str, title: str) -> float:
    """How well a link or heading names this role; 0.85 and up is the role.

    The role is the title's head without its term ("Software Engineering").
    A text missing the role's kind ("Intern") is another role, and a text that
    also has the title's term ("Summer 2027") or place is a closer one. Words
    the title does not have count against it, so a paragraph is not a title.
    """
    words = set(_tokens(text))
    if _tokens(text) == _tokens(title):
        return EXACT
    head = _head(title)
    role = _significant(TERM_WORDS.sub(" ", head)) or _significant(head)
    if not role:
        return 0.0
    score = len(role & words) / len(role)
    kinds = {"intern", "coop"} & set(_tokens(title))
    if kinds and not kinds & words:
        score *= 0.8
    rest = _significant(title) - role - kinds
    score -= 0.03 * min(len(rest - words), 3)
    extra = len(words - _significant(title))
    return score - min(extra, 10) * 0.02


LINKS_SCRIPT = r"""
() => [...document.querySelectorAll('a[href], [role="link"], [role="button"], button')]
  .map((node, index) => ({
    index,
    text: (node.innerText || node.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim(),
    href: node.href || '',
    visible: Boolean(node.offsetWidth || node.offsetHeight || node.getClientRects().length),
  }))
  .filter(item => item.visible && item.text && item.text.length <= 160)
"""
SELECTOR = 'a[href], [role="link"], [role="button"], button'


def _links(page: Page) -> list[dict[str, Any]]:
    try:
        return cast(list[dict[str, Any]], page.evaluate(LINKS_SCRIPT))
    except Error:
        return []


def _same_site(href: str, start: str) -> bool:
    if not href or href.startswith(("javascript:", "#")):
        return True
    host = urlsplit(href).hostname
    return host is None or registrable_site(href) == registrable_site(start)


def _click(page: Page, index: int) -> bool:
    try:
        page.locator(SELECTOR).nth(index).click(timeout=5_000)
        page.wait_for_timeout(RESULT_WAIT_MS)
        return True
    except Error:
        return False


def open_posting_link(page: Page, target: Target, *, start: str) -> bool:
    """Click the one link whose text is this role's title; False when none or several."""
    scored = [
        (title_score(str(item["text"]), target.title), item)
        for item in _links(page)
        if _same_site(str(item.get("href") or ""), start)
    ]
    scored = [entry for entry in scored if entry[0] >= 0.85]
    if not scored:
        return False
    best = max(score for score, _item in scored)
    top = [item for score, item in scored if score == best]
    # Several links to one place (a card and its title) are one result.
    destinations = {str(item.get("href") or item["text"]) for item in top}
    if len(destinations) != 1:
        return False
    return _click(page, int(top[0]["index"]))


def open_listing(page: Page, *, start: str) -> bool:
    """Follow the one link into the site's open roles, when there is one."""
    found = [
        item for item in _links(page)
        if LISTING_TRANSITION.search(str(item["text"]))
        and _same_site(str(item.get("href") or ""), start)
    ]
    destinations = {str(item.get("href") or item["text"]) for item in found}
    if len(destinations) != 1:
        return False
    return _click(page, int(found[0]["index"]))


SEARCH_BOX_SCRIPT = r"""
pattern => {
  const matches = new RegExp(pattern, 'i');
  const boxes = [...document.querySelectorAll(
    'input[type="search"], [role="searchbox"], input[type="text"], input:not([type])'
  )].filter(node => node.offsetWidth || node.offsetHeight || node.getClientRects().length);
  const named = node => [node.getAttribute('placeholder'), node.getAttribute('aria-label'),
    node.getAttribute('name'), node.id, node.getAttribute('type')].join(' ');
  const index = boxes.findIndex(node => node.type === 'search'
    || node.getAttribute('role') === 'searchbox' || matches.test(named(node)));
  if (index < 0) return null;
  const all = [...document.querySelectorAll('input, [role="searchbox"]')];
  return all.indexOf(boxes[index]);
}
"""


def search_box(page: Page) -> Any | None:
    """The site's job search box, when the page has exactly that."""
    try:
        index = page.evaluate(SEARCH_BOX_SCRIPT, SEARCH_BOX.pattern)
    except Error:
        return None
    if index is None:
        return None
    return page.locator('input, [role="searchbox"]').nth(int(index))


def search_for(page: Page, target: Target, *, start: str) -> bool:
    """Search the site by the full title, then shorter parts, and open the result."""
    for query in title_queries(target.title):
        box = search_box(page)
        if box is None:
            return False
        try:
            box.fill(query, timeout=5_000)
            box.press("Enter", timeout=5_000)
            page.wait_for_timeout(RESULT_WAIT_MS)
        except Error:
            continue
        if open_posting_link(page, target, start=start):
            return True
    return False


Route = Callable[[Page, Target], bool]


def find_posting(
    page: Page,
    target: Target,
    *,
    route: Route | None = None,
    report: Callable[[str], None] = lambda _line: None,
) -> bool:
    """Reach the posting from wherever the page is; True once the page names the role."""
    start = page.url

    def found() -> bool:
        text = rendered_body_text(page, timeout_ms=10_000,
                                  accept=lambda body: shows_title(body, target.title))
        return is_posting(page, target.title, text=text)

    if found():
        return True
    if route is not None:
        report("following the route written for this site")
        with suppress(Error):
            if route(page, target) and found():
                return True
    for _hop in range(MAX_HOPS):
        if open_posting_link(page, target, start=start):
            report("opened the link that names the role")
            if found():
                return True
            continue
        if search_for(page, target, start=start):
            report("searched the site for the role")
            if found():
                return True
            continue
        if open_listing(page, start=start):
            report("opened the site's open roles")
            continue
        break
    return found()


WATCH_SECONDS = 2.0


def reach_posting(
    browser: Any,
    page: Page,
    target: Target,
    *,
    report_page_failure: Callable[..., Any],
    sleep: Callable[[float], None] = time.sleep,
    fixes: Any | None = None,
    run_id: str = "",
    cdp_url: str = "",
) -> Page:
    """The posting's tab: found by its site's route or the navigator, else by you.

    What the navigator could not find is reported as `posting_not_found`, with
    what the page offered, and the run watches the browser until the posting is
    in front, so a run never ends because a URL landed on a careers home. With a
    fixer, the failure goes to it while you look: a route it writes and keeps is
    tried on this tab, and opens the posting for you.
    """
    from personal_os_worker.jobs.browser import visible_actions
    from personal_os_worker.jobs.registry.routes import route_for

    console.print("  [dim]this page is not the posting; looking for it on the site[/dim]")
    if find_posting(
        page, target, route=route_for(page.url),
        report=lambda line: console.print(f"  [dim]{line}[/dim]"),
    ):
        return page
    offered: list[str] = []
    with suppress(Error):
        offered = visible_actions(page)
    message = f"Neither a link, the site's search, nor its open roles led to {target.title!r}"
    issues = report_page_failure("posting_not_found", message, offered=offered) or []
    if fixes is not None:
        hand_to_fixer(page, target, fixes, message=message, offered=offered, run_id=run_id,
                      issue_id=str(issues[0].get("id")) if issues else None)
    console.print(
        f"[yellow]Posting not found:[/yellow] open {target.title!r} in the browser; "
        "I am watching the page."
    )
    while True:
        page = browser.active_page(page)
        # The route the fixer keeps opens the posting itself; no reload is needed.
        if fixes is not None and fixes.at_safe_point(partial(probe_args, page, cdp_url)):
            fixes.consume_kept()
        text = rendered_body_text(page, timeout_ms=2_000)
        if is_posting(page, target.title, text=text):
            console.print("  [dim]the posting is open[/dim]")
            return page
        sleep(WATCH_SECONDS)


def probe_args(page: Page, cdp_url: str) -> dict[str, Any]:
    from personal_os_worker.jobs.browser import cdp_target_id

    return {"cdp_url": cdp_url, "target_id": cdp_target_id(page), "page_url": page.url,
            "knowledge": {}}


def hand_to_fixer(
    page: Page,
    target: Target,
    fixes: Any,
    *,
    message: str,
    offered: list[str],
    run_id: str,
    issue_id: str | None,
) -> None:
    """A posting the navigator cannot find is a route the fixer can write."""
    from personal_os_worker.jobs.browser import inspect_fields, page_evidence
    from personal_os_worker.jobs.fix.lane import Problem

    files: dict[str, bytes] = {}
    with suppress(Error):
        files = page_evidence(page, inspect_fields(page))
    fixes.submit(Problem(
        key=f"posting_not_found|{registrable_site(page.url)}",
        cause="posting_not_found",
        question=target.title,
        note=message,
        target={"kind": "route", "goal": "posting", "title": target.title,
                "company": target.company},
        evidence={"url": page.url, "offered": offered},
        tried=("a link naming the role, the site's search by the full and shorter titles, "
               "and its open roles",),
        issue_id=issue_id,
        run_id=run_id or None,
        page_files=files,
    ))
