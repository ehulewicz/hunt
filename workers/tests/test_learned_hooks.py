"""Hook points: a site's learned declarations run only after the built-ins fail."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import patch_package, patch_session
from playwright.sync_api import Error, sync_playwright

import personal_os_worker.jobs.registry as registry


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


@pytest.fixture()
def learned_package(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """A temporary learned package mounted over every category's `learned/`."""
    monkeypatch.setattr(
        "personal_os_worker.jobs.navigate.registrable_site",
        lambda _url: "fixture.example",
    )
    patch_package(monkeypatch, "personal_os_worker.jobs.browser",
                  "registrable_site", lambda _url: "fixture.example")
    patch_session(monkeypatch, "registrable_site", lambda _url: "fixture.example")
    registry.reload_learned()
    packages = {}
    for category in ("kinds", "strategies", "formats", "questions", "routes"):
        folder = tmp_path / category
        folder.mkdir()
        (folder / "__init__.py").write_text("")
        packages[category] = folder
        package = __import__(
            f"personal_os_worker.jobs.registry.{category}.learned", fromlist=["x"]
        )
        monkeypatch.setattr(package, "__path__", [str(folder)])
    yield packages
    registry.reload_learned()


def _write(folder: Path, name: str, source: str) -> Path:
    path = folder / f"{name}.py"
    path.write_text(source)
    return path


CUSTOM_WIDGET = """
<div class="odd-widget" data-state="idle">
  <button class="odd-btn" type="button">pick</button>
</div>
"""


def test_a_learned_kind_is_captured_and_filled(browser_page,
    learned_package):  # type: ignore[no-untyped-def]
    _write(
        learned_package["kinds"],
        "odd",
        """from personal_os_worker.jobs.registry.api import Kind
KIND = Kind(
    name="odd_widget", selector=".odd-widget", reads="attribute:data-state",
    strategies=("odd_pick",), sites=("fixture.example",), structure="odd_widget",
)
""",
    )
    registry.reload_learned()
    from personal_os_worker.jobs.browser import inspect_fields

    browser_page.set_content(CUSTOM_WIDGET)
    fields = inspect_fields(browser_page)
    odd = [field for field in fields if field.get("custom") == "odd_widget"]
    assert len(odd) == 1
    assert odd[0]["custom_index"] == 0
    assert odd[0]["fingerprint"].startswith("custom|odd_widget|")


def test_a_question_reader_names_a_radio_group(browser_page,
    learned_package):  # type: ignore[no-untyped-def]
    _write(
        learned_package["questions"],
        "lead",
        """from personal_os_worker.jobs.registry.api import QuestionReader
READER = QuestionReader(
    name="lead", group_selector="div.q-block", question_selector="p.q",
    sites=("fixture.example",),
)
""",
    )
    registry.reload_learned()
    from personal_os_worker.jobs.browser import inspect_fields

    browser_page.set_content(
        '<div class="q-block"><p class="q">Are you 18 or older?</p>'
        '<input type="radio" name="a" value="y"><input type="radio" name="a" value="n"></div>'
    )
    fields = inspect_fields(browser_page)
    groups = {str(field.get("group") or "") for field in fields}
    assert "Are you 18 or older?" in groups


def test_a_learned_strategy_only_after_builtins_fail(browser_page,
    learned_package):  # type: ignore[no-untyped-def]
    """A kind no built-in knows: only the site's learned strategy can fill it."""
    _write(
        learned_package["kinds"],
        "odd",
        """from personal_os_worker.jobs.registry.api import Kind
KIND = Kind(
    name="odd_widget", selector=".odd-widget", reads="attribute:data-state",
    strategies=("odd_mark",), sites=("fixture.example",), structure="odd_widget",
)
""",
    )
    _write(
        learned_package["strategies"],
        "oddmark",
        """from personal_os_worker.jobs.registry.api import Strategy


def mark(widget, value):
    widget.click()
    return None


STRATEGY = Strategy(
    name="odd_mark", kind="odd_widget",
        operate=mark, sites=("fixture.example",), structure="odd_widget",
)
""",
    )
    registry.reload_learned()
    from personal_os_worker.jobs.autofill import AutofillPlan, FormFill
    from personal_os_worker.jobs.browser import apply_autofill, inspect_fields

    browser_page.set_content(
        """<div class="odd-widget" data-state="idle" role="button" tabindex="0">pick</div>
<script>
document.querySelector('.odd-widget').addEventListener('click', event => {
  event.currentTarget.dataset.state = 'picked';
});
</script>"""
    )
    fields = inspect_fields(browser_page)
    index = next(i for i, f in enumerate(fields) if f.get("custom") == "odd_widget")
    plan = AutofillPlan(
        fills=(FormFill(index=index, kind="text", value="picked"),),
        unanswered_required_questions=(),
    )
    outcome = apply_autofill(
        browser_page, fields, plan, resume_path=None, assert_active=lambda: None
    )
    assert not outcome["failures"], outcome["failures"]
    assert (
        browser_page.locator(".odd-widget").get_attribute("data-state") == "picked"
    )


def test_a_learned_format_runs_only_when_builtins_miss_and_only_for_its_site(
    learned_package,  # type: ignore[no-untyped-def]
) -> None:
    _write(
        learned_package["formats"],
        "backwards",
        """from personal_os_worker.jobs.registry.api import Format


def backwards(render_input):
    text = str(render_input.value or "")
    return text[::-1] or None


FORMAT = Format(
    name="backwards", fact_keys=("contact.full_name",), render=backwards,
    sites=("a.com",),
)
""",
    )
    registry.reload_learned()
    from personal_os_worker.jobs.registry.formats import render

    # Built-ins miss every option offered: the site's learned format runs.
    field = {"tag": "select", "type": "", "label": "Name", "options": ["A", "B"]}
    reversed_it = render(
        "contact.full_name", "Ethan", "text",
        field=field, question="Your name", site="a.com",
    )
    assert reversed_it is not None
    assert reversed_it.value == "nahtE"
    assert reversed_it.formatter.startswith("learned:")
    # Another site: no learned file applies, so nothing renders.
    normal = render(
        "contact.full_name", "Ethan", "text",
        field=field, question="Your name", site="b.com",
    )
    assert normal is None
    # No site at all: built-ins only.
    plain = render(
        "contact.full_name", "Ethan", "text", field=field, question="Your name",
    )
    assert plain is None
    # A control the built-in can render: the learned file never runs.
    plain_field = {"tag": "input", "type": "text", "label": "Name"}
    built = render(
        "contact.full_name", "Ethan", "text",
        field=plain_field, question="Your name", site="a.com",
    )
    assert built is not None and built.value == "Ethan"


def test_the_policy_refuses_a_file_before_import(learned_package,
    tmp_path):  # type: ignore[no-untyped-def]
    _write(
        learned_package["kinds"],
        "sneaky",
        """import os
from personal_os_worker.jobs.registry.api import Kind
KIND = Kind(
    name="x", selector="s", reads="value",
        strategies=(), sites=("fixture.example",), structure="odd_widget",
)
""",
    )
    registry.reload_learned()
    assert registry.learned("kinds", "fixture.example") == []
    refused = registry.refused()
    assert any("import os" in item for key in refused for item in refused[key])


def test_a_learned_route_reaches_a_posting(browser_page,
    learned_package):  # type: ignore[no-untyped-def]
    _write(
        learned_package["routes"],
        "posting",
        """from personal_os_worker.jobs.registry.api import Route


def reach(site_handle, title, company):
    return site_handle.click_text("Open role")


ROUTE = Route(sites=("fixture.example",), goal="posting", run=reach)
""",
    )
    registry.reload_learned()
    from personal_os_worker.jobs.navigate import Target, find_posting

    browser_page.set_content(
        """<a href="#" id="job">Open role</a>
<script>
document.querySelector('#job').addEventListener('click', event => {
  event.preventDefault();
  document.body.innerHTML = '<h1>Backend Intern</h1><p>Acme Corp</p>';
});
</script>"""
    )
    target = Target(title="Backend Intern", company="Acme Corp")
    assert find_posting(browser_page, target) is True
    assert "Backend Intern" in browser_page.locator("body").inner_text()


def test_a_learned_route_reaches_the_form_on_the_wall_path(
    browser_page, learned_package, monkeypatch
) -> None:
    _write(
        learned_package["routes"],
        "form",
        """from personal_os_worker.jobs.registry.api import Route


def reach(site_handle, title, company):
    return site_handle.click_text("Apply")


ROUTE = Route(sites=("fixture.example",), goal="form", run=reach)
""",
    )
    registry.reload_learned()
    from types import SimpleNamespace

    from personal_os_worker.jobs.session import AutofillSession

    browser_page.set_content(
        """<a href="#" id="apply">Apply</a>
<script>
document.querySelector('#apply').addEventListener('click', event => {
  event.preventDefault();
  document.body.innerHTML = '<form>'
    + '<label>Name <input name=full_name required></label>'
    + '<label>Email <input name=email type=email required></label>'
    + '<label>Phone <input name=phone type=tel></label>'
    + '<button>Submit application</button></form>';
});
</script>"""
    )
    session = SimpleNamespace(
        page=browser_page, state=SimpleNamespace(navigation=None),
    )
    assert AutofillSession._learned_form_route(session) is True  # type: ignore[arg-type]


def test_replay_learned_loads_the_file_as_its_site(learned_package, tmp_path,
    monkeypatch):  # type: ignore[no-untyped-def]
    path = _write(
        learned_package["formats"],
        "backwards",
        """from personal_os_worker.jobs.registry.api import Format


def backwards(render_input):
    text = str(render_input.value or "")
    return text[::-1] or None


FORMAT = Format(
    name="backwards", fact_keys=("contact.full_name",), render=backwards,
    sites=("fixture.example",),
)
""",
    )
    registry.reload_learned()
    from types import SimpleNamespace

    from personal_os_worker.jobs.replay import check_code
    from personal_os_worker.jobs.resolve import Knowledge

    fake_api = SimpleNamespace(
        sightings=lambda: [],
        patterns=lambda *_args, **_kwargs: [],
        close=lambda: None,
    )
    monkeypatch.setattr(
        "personal_os_worker.jobs.repair.commands.knowledge_for",
        lambda _api: Knowledge(),
    )
    replay = check_code(fake_api, learned=str(path))
    assert replay is not None
    assert [item.name for item in registry.learned("formats", "fixture.example")] == [
        "backwards"
    ]
