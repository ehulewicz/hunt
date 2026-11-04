"""Finding the posting on its own site: a careers home, a job list, a search."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Error, Route, sync_playwright

from personal_os_worker.jobs.navigate import (
    ZERO_RESULTS,
    Searched,
    Target,
    find_posting,
    is_posting,
    reach_posting,
    shows_title,
    title_queries,
    title_score,
)
from personal_os_worker.jobs.posting_page import PostingClosed
from personal_os_worker.jobs.registry import routes

TITLE = "Software Engineering Intern (Summer 2027) - Redmond"
SITE = "https://careers.example.com"
HOME = f"""<html><head><title>Careers at Example</title></head><body>
<header><a href="{SITE}/about">About Example</a></header>
<h1>Build what's next at Example</h1>
<p>Internships, early careers and more at Example.</p>
<a href="{SITE}/jobs">See open roles</a>
<a href="https://elsewhere.example.org/jobs">Partner jobs</a>
</body></html>"""
POSTING = f"""<html><head><title>{TITLE} | Example</title></head><body>
<h1>Software Engineering Intern (Summer 2027) - Redmond</h1>
<p>Join our platform team for the summer.</p><a href="{SITE}/apply/123">Apply</a>
</body></html>"""


def jobs_page(query: str) -> str:
    """A search that, like many, finds nothing for a full title with its extras."""
    results = ""
    if query and "summer" not in query.casefold() and "software" in query.casefold():
        results = (
            f'<ul><li><a href="{SITE}/jobs/123">Software Engineering Intern</a> Redmond</li>'
            f'<li><a href="{SITE}/jobs/124">Software Engineering Manager</a></li></ul>'
        )
    elif query:
        results = "<p>No results.</p>"
    return f"""<html><head><title>Search jobs | Example</title></head><body>
<h1>Search jobs</h1>
<form action="{SITE}/jobs" method="get">
  <input type="search" name="q" placeholder="Search by keyword" value="{query}">
</form>{results}
<a href="{SITE}/jobs?q=software+engineering">Software Engineering</a>
</body></html>"""


def serve(route: Route) -> None:
    url = urlsplit(route.request.url)
    query = parse_qs(url.query).get("q", [""])[0]
    body = {"/": HOME, "/jobs": jobs_page(query), "/jobs/123": POSTING}.get(url.path)
    if body is None:
        route.fulfill(status=404, body="not found")
    else:
        route.fulfill(content_type="text/html", body=body)


@pytest.fixture(scope="module")
def browser():  # type: ignore[no-untyped-def]
    with sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(headless=True)
        except Error as error:
            pytest.skip(f"browser launch is restricted: {error}")
        yield launched
        launched.close()


@pytest.fixture
def page(browser):  # type: ignore[no-untyped-def]
    context = browser.new_context()
    context.route(f"{SITE}/**", serve)
    page = context.new_page()
    yield page
    context.close()


def test_title_queries_run_from_the_whole_title_to_its_role_words() -> None:
    assert title_queries(TITLE) == [
        TITLE,
        "Software Engineering Intern - Redmond",
        "Software Engineering Intern",
        "Software Engineering",
    ]


def test_a_link_naming_the_role_scores_above_a_category_or_another_role() -> None:
    exact = title_score("Software Engineering Intern (Summer 2027) - Redmond", TITLE)
    role = title_score("Software Engineering Intern", TITLE)
    assert exact > role >= 0.85
    assert title_score("Software Engineering Internship, Redmond", TITLE) > role
    assert role > title_score("Software Engineering Manager", TITLE)
    assert title_score("Software Engineering", TITLE) < 0.85
    assert title_score("Hardware Engineering Intern", TITLE) < 0.85
    assert shows_title(f"Apply now: {TITLE}!", TITLE)
    assert not shows_title("Software Engineering Intern", TITLE)


def test_a_careers_home_is_not_the_posting_and_the_posting_is(page: Any) -> None:
    page.goto(f"{SITE}/")
    assert not is_posting(page, TITLE)
    page.goto(f"{SITE}/jobs/123")
    assert is_posting(page, TITLE)


def test_the_navigator_opens_roles_then_searches_shorter_parts_of_the_title(page: Any) -> None:
    page.goto(f"{SITE}/")
    lines: list[str] = []
    assert find_posting(page, Target(TITLE, "Example"), report=lines.append)
    assert page.url == f"{SITE}/jobs/123"
    assert lines == ["opened the site's open roles", "searched the site for the role"]


def test_a_route_written_for_the_site_goes_first(page: Any) -> None:
    calls: list[str] = []

    def straight_there(page: Any, target: Target) -> bool:
        calls.append(target.title)
        page.goto(f"{SITE}/jobs/123")
        return True

    page.goto(f"{SITE}/")
    assert find_posting(page, Target(TITLE, "Example"), route=straight_there)
    assert calls == [TITLE]


def test_routes_are_found_by_site(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "ROUTES", {})
    monkeypatch.setattr(routes, "_loaded", True)

    @routes.route("example.com")
    def anything(page: Any, target: Target) -> bool:
        return False

    assert routes.route_for(f"{SITE}/jobs") is anything
    assert routes.route_for("https://other.example.org/") is None


def test_a_posting_nothing_finds_is_reported_and_watched_for(page: Any) -> None:
    page.goto(f"{SITE}/about")
    reported: list[tuple[str, list[str]]] = []

    class Browser:
        def active_page(self, current: Any) -> Any:
            return current

    def you_open_it(_seconds: float) -> None:
        page.goto(f"{SITE}/jobs/123")

    found = reach_posting(
        Browser(), page, Target(TITLE, "Example"),
        report_page_failure=lambda category, message, **kw: reported.append(
            (category, kw["offered"])),
        sleep=you_open_it,
    )
    assert found is page and page.url == f"{SITE}/jobs/123"
    assert [category for category, _ in reported] == ["posting_not_found"]


# ----- a posting that is gone leaves; one that may still exist stays ---------------

BOARD = "https://job-boards.example.com/knowbe4"
GONE_TITLE = "Software Engineer Summer Internship"


def board(query: str, *, said_before: bool = False, keeps_query: bool = True) -> str:
    """A company board: the whole title finds nothing, a shorter part finds another role."""
    if not query:
        results = "<p>0 jobs</p>" if said_before else "<p>7 jobs</p>"
    elif "summer" in query.casefold():
        results = "<p>0 jobs</p>"
    else:
        results = f'<p>1 job</p><a href="{BOARD}/1">Staff Software Engineer</a>'
    shown = query if keeps_query else ""
    return f"""<html><head><title>Current openings at KnowBe4</title></head><body>
<h1>Current openings at KnowBe4</h1>
<form action="{BOARD}" method="get">
  <input type="search" name="q" placeholder="Search" value="{shown}">
</form>{results}</body></html>"""


def board_page(browser: Any, **kwargs: Any) -> Any:
    context = browser.new_context()

    def respond(route: Route) -> None:
        query = parse_qs(urlsplit(route.request.url).query).get("q", [""])[0]
        route.fulfill(content_type="text/html", body=board(query, **kwargs))

    context.route(f"{BOARD}**", respond)
    page = context.new_page()
    page.goto(BOARD)
    return page


class StopWatching(Exception):
    pass


def reach(page: Any, reported: list[str]) -> Any:
    class Browser:
        def active_page(self, current: Any) -> Any:
            return current

    def stop(_seconds: float) -> None:
        raise StopWatching

    return reach_posting(
        Browser(), page, Target(GONE_TITLE, "KnowBe4"),
        report_page_failure=lambda category, _message, **_kw: reported.append(category),
        sleep=stop,
    )


def test_a_search_that_went_in_and_found_nothing_means_the_posting_is_gone(browser: Any) -> None:
    page = board_page(browser)
    reported: list[str] = []
    try:
        with pytest.raises(PostingClosed, match="went in and found nothing"):
            reach(page, reported)
        assert reported == ["closed_posting"]
    finally:
        page.context.close()


@pytest.mark.parametrize(
    "board_says",
    [
        pytest.param({"keeps_query": False}, id="the box did not hold the query"),
        pytest.param({"said_before": True}, id="the page said none before the search"),
    ],
)
def test_a_search_that_proves_nothing_keeps_the_row(
    browser: Any, board_says: dict[str, Any]
) -> None:
    page = board_page(browser, **board_says)
    reported: list[str] = []
    try:
        with pytest.raises(StopWatching):
            reach(page, reported)
        assert reported == ["posting_not_found"]
    finally:
        page.context.close()


def test_a_notice_the_page_shows_during_the_watch_settles_it(page: Any) -> None:
    page.goto(f"{SITE}/about")
    reported: list[str] = []

    class Browser:
        def active_page(self, current: Any) -> Any:
            return current

    def the_site_says_so(_seconds: float) -> None:
        page.set_content("<p>The job you are looking for is no longer open.</p>")

    with pytest.raises(PostingClosed, match="no longer open"):
        reach_posting(
            Browser(), page, Target(TITLE, "Example"),
            report_page_failure=lambda category, _message, **_kw: reported.append(category),
            sleep=the_site_says_so,
        )
    assert reported == ["posting_not_found", "closed_posting"]


def test_a_search_needs_every_query_and_the_whole_title_to_prove_a_posting_gone() -> None:
    queries = title_queries(GONE_TITLE)
    assert len(queries) > 1
    assert Searched(set(queries), {queries[0]}).proves_gone(GONE_TITLE)
    assert not Searched(set(queries), {queries[-1]}).proves_gone(GONE_TITLE)
    assert not Searched({queries[0]}, {queries[0]}).proves_gone(GONE_TITLE)


@pytest.mark.parametrize(
    ("text", "zero"),
    [
        ("0 jobs", True),
        ("Showing 0 results", True),
        ("No results.", True),
        ("No jobs found", True),
        ("No open positions available", True),
        ("Your search did not match any jobs", True),
        ("10 jobs", False),
        ("No job alerts yet", False),
        ("Search jobs", False),
    ],
)
def test_what_counts_as_a_search_finding_nothing(text: str, zero: bool) -> None:
    assert (ZERO_RESULTS.search(text) is not None) is zero
