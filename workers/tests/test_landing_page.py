"""The landing page: waiting for it to render, identifying it, and reusing its tab."""

from __future__ import annotations

import time
from urllib.parse import quote

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.browser import (
    identifies_posting,
    rendered_body_text,
    tab_continues,
)

COMPANY = "Northrop Grumman"
TITLE = "2027 S/W Embedded Engineering Intern - Annapolis MD"
JOB_URL = (
    "https://ngc.wd1.myworkdayjobs.com/en-US/northrop_grumman_external_site/job/"
    "United-States-Maryland-Annapolis/XMLNAME-2027-S-W-Embedded-Engineering-Intern"
    "---Annapolis-MD_R10254697"
)

# Workday serves an empty root and fills it from script, which is why reading the
# body straight after `domcontentloaded` saw `<div id="root"></div>` and nothing else.
SPA_SHELL = """
<div id="root"></div>
<script>
setTimeout(() => {
  document.querySelector('#root').textContent =
    'Welcome to the Northrop Grumman Candidate Portal. ' +
    '2027 S/W Embedded Engineering Intern - Annapolis MD. Apply. ' +
    'An internship at Northrop Grumman is unique.';
}, 900);
</script>
"""


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
    page = context.new_page()
    yield page
    context.close()


def test_an_empty_shell_identifies_nothing() -> None:
    assert identifies_posting("", company=COMPANY, title=TITLE) is False


def test_the_rendered_page_identifies_the_posting() -> None:
    body = "Welcome to the Northrop Grumman Candidate Portal."
    assert identifies_posting(body, company=COMPANY, title=TITLE) is True


def test_another_employers_page_identifies_nothing() -> None:
    body = "Welcome to Lockheed Martin. Senior Accountant, Fort Worth TX."
    assert identifies_posting(body, company=COMPANY, title=TITLE) is False


def test_reading_waits_for_a_single_page_app_to_render(page) -> None:  # type: ignore[no-untyped-def]
    page.goto("data:text/html," + quote(SPA_SHELL), wait_until="domcontentloaded")
    assert page.locator("body").inner_text().strip() == ""  # the shell, as captured
    text = rendered_body_text(page, timeout_ms=10_000)
    assert "Northrop Grumman" in text
    assert identifies_posting(text, company=COMPANY, title=TITLE) is True


def test_reading_an_already_rendered_page_does_not_wait_out_the_timeout(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content("<p>Welcome to the Northrop Grumman Candidate Portal.</p>")
    started = time.monotonic()
    text = rendered_body_text(page, timeout_ms=30_000)
    assert "Northrop Grumman" in text
    assert time.monotonic() - started < 5


def test_a_match_returns_as_soon_as_the_page_names_the_posting(page) -> None:  # type: ignore[no-untyped-def]
    page.goto("data:text/html," + quote(SPA_SHELL), wait_until="domcontentloaded")
    started = time.monotonic()
    text = rendered_body_text(
        page,
        timeout_ms=10_000,
        accept=lambda body: identifies_posting(body, company=COMPANY, title=TITLE),
    )
    assert "Northrop Grumman" in text
    assert time.monotonic() - started < 5


def test_the_tab_on_this_application_continues_it() -> None:
    assert tab_continues(JOB_URL, JOB_URL) is True
    assert tab_continues(f"{JOB_URL}?source=linkedin", JOB_URL) is True
    # Every major board keeps the posting's path once the form opens.
    assert tab_continues(f"{JOB_URL}/apply/applicationManager/1422", JOB_URL) is True


def test_another_tab_never_continues_this_application() -> None:
    other_job = JOB_URL.replace("R10254697", "R10254698")
    assert tab_continues(other_job, JOB_URL) is False
    assert tab_continues("https://ngc.wd1.myworkdayjobs.com/en-US", JOB_URL) is False
    assert tab_continues("about:blank", JOB_URL) is False
    assert tab_continues("https://example.invalid/apply", JOB_URL) is False


def test_a_posting_served_inside_an_iframe_is_read_through_it(page) -> None:  # type: ignore[no-untyped-def]
    """iCIMS: the page says "Skip Branding" and the posting lives in a frame."""
    inner = quote("<h1>Software Engineer Intern</h1><p>Northrop Grumman, San Antonio TX</p>")
    page.set_content(
        f'<a href="#x">Skip Branding</a><iframe src="data:text/html,{inner}"></iframe>'
    )
    page.wait_for_timeout(500)
    text = rendered_body_text(
        page,
        timeout_ms=5_000,
        accept=lambda body: identifies_posting(body, company=COMPANY, title=TITLE),
    )
    assert "Skip Branding" in text and "Northrop Grumman" in text
