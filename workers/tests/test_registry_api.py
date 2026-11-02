"""The learned-file API: handles over Playwright with nothing Playwright-shaped out."""

from __future__ import annotations

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.registry.api import (
    Refused,
    SiteHandle,
    WidgetHandle,
)


@pytest.fixture(scope="module")
def browser_page():  # type: ignore[no-untyped-def]
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as error:
            pytest.skip(f"browser launch is restricted: {error}")
        page = browser.new_page()
        yield page
        browser.close()


def _widget(page, selector="input"):
    return WidgetHandle(page, page.locator(selector).first)


def test_a_handle_types_reads_and_waits(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content('<input id="a" value="">')
    handle = _widget(browser_page, "#a")
    handle.type("hello")
    assert handle.text() == "hello"
    assert handle.attribute("id") == "a"
    handle.wait(10)
    assert handle.wait_for_text("a", timeout_ms=200) is False


def test_a_handle_picks_a_native_option_and_reads_selected(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        '<select><option>One</option><option>Two</option></select>'
    )
    handle = _widget(browser_page, "select")
    assert handle.pick("Two") == "Two"
    assert handle.selected() == ["Two"]


def test_a_handle_presses_only_allowed_keys(browser_page) -> None:  # type: ignore[no-untyped-def]
    browser_page.set_content('<input id="a" value="x">')
    handle = _widget(browser_page, "#a")
    handle.press("Backspace")
    with pytest.raises(Refused):
        handle.press("Enter")
    with pytest.raises(Refused):
        handle.press("F5")


def test_clicking_a_submit_or_next_control_is_refused(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        '<button id="s">Submit application</button><button id="n">Next</button>'
    )
    with pytest.raises(Refused):
        _widget(browser_page, "#s").click()
    with pytest.raises(Refused):
        _widget(browser_page, "#n").click()
    with pytest.raises(Refused):
        _widget(browser_page, "#s").pick("x")


def test_a_site_handle_finds_links_text_and_refuses_submit(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        '<a href="/jobs/1">Backend Intern</a>'
        '<a href="/post">Submit application</a>'
        '<p>hello page</p>'
    )
    handle = SiteHandle(browser_page)
    assert "Backend Intern" in handle.links()
    assert "Submit application" in handle.links()
    assert "hello page" in handle.text()
    assert handle.url().startswith("about:")
    # A submit-reading link is never clicked.
    assert handle.click_text("Submit application") is False
    # Off-site navigation is refused.
    assert handle.open("https://elsewhere.example/x") is False


def test_the_loader_finds_a_sites_declarations(tmp_path, monkeypatch) -> None:
    import personal_os_worker.jobs.registry as registry
    package = tmp_path / "learned_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "a_kind.py").write_text(
        "from personal_os_worker.jobs.registry.api import Kind\n"
        "KIND = Kind(name='slider', selector='[role=slider]', reads='value',\n"
        "            strategies=('slide',), structure='slider',\n"
        "            sites=('Acme.com',))\n"
    )
    (package / "b_kind.py").write_text(
        "from personal_os_worker.jobs.registry.api import Kind\n"
        "KIND = Kind(name='dial', selector='[role=dial]', reads='value',\n"
        "            strategies=('turn',), structure='dial', sites=('other.com',))\n"
    )
    kinds_learned = __import__(
        "personal_os_worker.jobs.registry.kinds.learned", fromlist=["x"]
    )
    monkeypatch.setattr(kinds_learned, "__path__", [str(package)])
    monkeypatch.setattr(
        "personal_os_worker.jobs.registry.kinds.learned", kinds_learned
    )
    registry.reload_learned()
    found = registry.learned("kinds", "acme.com")
    assert [item.name for item in found] == ["slider"]
    assert registry.learned("kinds", "other.com")[0].name == "dial"
    registry.reload_learned()
