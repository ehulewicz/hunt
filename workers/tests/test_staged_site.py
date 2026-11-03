"""Keep the manual self-building run's static site honest as the engine changes."""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.audit import classify_unknown
from personal_os_worker.jobs.browser import inspect_fields, inspect_unknown_controls
from personal_os_worker.jobs.navigate import Target, find_posting, reach_posting

SITE = Path(__file__).parent / "fixtures/staged_site"
TITLE = "Software Engineering Intern (Summer 2027)"


@pytest.fixture(scope="module")
def staged_site():  # type: ignore[no-untyped-def]
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

    handler = partial(QuietHandler, directory=str(SITE))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


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
    yield context.new_page()
    context.close()


@pytest.mark.parametrize("navigator", ["find", "reach"])
def test_navigator_reaches_the_software_posting(
    page: Any, staged_site: str, navigator: str,
) -> None:
    page.goto(f"{staged_site}/index.html")
    target = Target(TITLE, "Staged Robotics")
    if navigator == "find":
        assert find_posting(page, target)
    else:
        assert reach_posting(None, page, target, report_page_failure=lambda *a, **k: pytest.fail(
            "the staged posting must be reachable without a fixer")) is page
    assert page.url == f"{staged_site}/jobs/101.html"
    assert page.get_by_role("heading", name=TITLE, exact=True).count() == 1
    page.get_by_role("link", name="Apply", exact=True).click()
    assert page.url == f"{staged_site}/apply.html"


def test_only_the_two_custom_widgets_are_unknown(page: Any, staged_site: str) -> None:
    page.goto(f"{staged_site}/apply.html")
    controls = inspect_unknown_controls(page)
    assert [(item["question"], item["role"]) for item in controls] == [
        ("Years of programming", "slider"), ("Preferred team", "radiogroup"),
    ]
    assert [(item.question, item.cause) for item in classify_unknown(controls, set())] == [
        ("Years of programming", "unknown_control"), ("Preferred team", "unknown_control"),
    ]
    fields = inspect_fields(page)
    assert {field["tag"] for field in fields} >= {"input", "textarea", "select"}
    assert page.get_by_label("Resume").get_attribute("type") == "file"


def test_custom_widgets_work_with_keys_and_clicks_and_submit_is_inert(
    page: Any, staged_site: str,
) -> None:
    page.goto(f"{staged_site}/apply.html")
    slider = page.get_by_role("slider", name="Years of programming")
    slider.focus()
    slider.press("ArrowRight")
    assert slider.get_attribute("aria-valuenow") == "1"
    slider.press("End")
    assert slider.get_attribute("aria-valuenow") == "10"
    slider.click(position={"x": 1, "y": 10})
    assert slider.get_attribute("aria-valuenow") == "0"
    research = page.get_by_role("radio", name="Research", exact=True)
    research.click()
    assert research.get_attribute("aria-checked") == "true"
    research.press("ArrowRight")
    platform = page.get_by_role("radio", name="Platform", exact=True)
    assert platform.get_attribute("aria-checked") == "true"
    assert research.get_attribute("aria-checked") == "false"
    platform.press("Home")
    assert page.get_by_role("radio", name="Autonomy", exact=True).get_attribute(
        "aria-checked") == "true"
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.get_by_role("button", name="Submit application").click()
    page.wait_for_timeout(100)
    assert requests == []
    assert page.url == f"{staged_site}/apply.html"


def test_the_scan_skips_widgets_a_learned_kind_captures(
    page: Any, staged_site: str, tmp_path: Path
) -> None:
    from personal_os_worker.jobs import registry
    from personal_os_worker.jobs.browser import registrable_site

    page.goto(f"{staged_site}/apply.html")
    site = registrable_site(page.url)

    def kind_source(name: str, selector: str) -> str:
        return f'''from personal_os_worker.jobs.registry.api import Kind

KIND = Kind(
    name="{name}",
    selector="{selector}",
    reads="attribute:aria-valuenow",
    strategies=("click_div",),
    sites=("{site}",),
    structure="x",
    proven=False,
    examples=(),
)
'''

    good = tmp_path / "years_slider.py"
    good.write_text(kind_source("years_slider", "#years"))
    bad = tmp_path / "bad_kind.py"
    bad.write_text(kind_source("bad_kind", "div["))
    registry.reload_learned()
    try:
        registry.load_file(good, category="kinds")
        registry.load_file(bad, category="kinds")
        # the learned kind's slider is skipped; a bad selector is just no match
        assert [c["question"] for c in inspect_unknown_controls(page)] == [
            "Preferred team"
        ]
    finally:
        registry.reload_learned()


def test_a_route_probe_reports_gone_when_the_goal_is_met(
    page: Any, staged_site: str
) -> None:
    from personal_os_worker.jobs.fix import probe

    page.goto(f"{staged_site}/jobs/101.html")
    result = probe.probe_route(
        page, {"goal": "posting", "title": TITLE, "company": "Staged Robotics"}
    )
    assert result["ok"] is False and result["gone"] is True
