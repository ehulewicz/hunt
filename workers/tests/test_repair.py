"""Repair (design §10.3): retry one field, never submit, return the rule that worked."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.browser import OPERATIONS, Attempt, inspect_fields
from personal_os_worker.jobs.repair.repairer import (
    find_field,
    repair_format,
    repair_on_page,
    repair_strategy,
)
from personal_os_worker.jobs.resolve import Knowledge
from personal_os_worker.jobs.safety import armed, install_guards, names_a_submission

TERMS_TYPEAHEAD = """
<div class="select">
  <label for="grad">Expected graduation</label>
  <div class="select__control"><div class="select__value-container">
    <input id="grad" class="select__input" role="combobox" autocomplete="off">
  </div></div>
  <div class="select__menu"></div>
</div>
<script>
(() => {
const terms = ['Spring 2027', 'Fall 2027', 'Spring 2028'];
const box = document.querySelector('#grad');
const holder = document.querySelector('.select__value-container');
const menu = document.querySelector('.select__menu');
const render = () => {
  const needle = box.value.toLowerCase();
  menu.innerHTML = '';
  if (!needle) return;  // a search box: nothing listed until you type
  terms.filter(term => term.toLowerCase().includes(needle)).forEach(term => {
    const option = document.createElement('div');
    option.setAttribute('role', 'option');
    option.textContent = term;
    option.addEventListener('mousedown', () => {
      const chosen = document.createElement('div');
      chosen.className = 'select__single-value';
      chosen.textContent = term;
      holder.prepend(chosen);
      box.value = '';
      menu.innerHTML = '';
    });
    menu.appendChild(option);
  });
};
box.addEventListener('input', render);
box.addEventListener('keydown', event => { if (event.key === 'Escape') menu.innerHTML = ''; });
})();
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


def test_the_page_guard_cancels_every_submission(page) -> None:  # type: ignore[no-untyped-def]
    install_guards(page)
    # A navigation, as repair and validate make: init scripts run before the page's own.
    page.goto("data:text/html," + quote(
        """
        <form id="f" action="https://example.invalid/apply/submit" method="post"
              onsubmit="window.sent = (window.sent || 0) + 1;">
          <input name="email"><button type="submit">Submit application</button>
          <button type="button" id="other">Yes</button>
        </form>
        <script>document.querySelector('#other').addEventListener('click',
          () => { window.clicked = true; });</script>
        """
    ))
    assert page.evaluate("window.__personalOsSubmitGuard") is True
    page.click("text=Submit application")
    page.evaluate("document.querySelector('#f').submit()")
    page.evaluate("document.querySelector('#f').requestSubmit()")
    page.click("#other")
    assert page.evaluate("window.sent || 0") == 0
    assert page.evaluate("window.clicked") is True  # ordinary buttons still work


def test_your_tab_is_guarded_only_while_the_worker_operates_it(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        """
        <form id="f" onsubmit="event.preventDefault(); window.sent = (window.sent || 0) + 1;">
          <input name="email"><button type="submit">Submit application</button>
        </form>
        """
    )
    with armed(page):
        page.click("text=Submit application")
        page.evaluate("document.querySelector('#f').requestSubmit()")
        assert page.evaluate("window.sent || 0") == 0
    # Handed back: your own Submit goes through.
    page.click("text=Submit application")
    assert page.evaluate("window.sent || 0") == 1
    with armed(page):
        page.click("text=Submit application")
    assert page.evaluate("window.sent || 0") == 1


def test_the_network_guard_refuses_what_names_a_submission() -> None:
    assert names_a_submission("POST", "https://boards.greenhouse.io/x/jobs/1/submit", None)
    assert names_a_submission(
        "POST", "https://jobs.ashbyhq.com/api/graphql",
        '{"operationName":"ApiSubmitSingleApplicationFormAction"}',
    )
    assert names_a_submission("POST", "https://jobs.lever.co/acme/1/apply", "")
    assert not names_a_submission("GET", "https://example.com/submit", None)
    assert not names_a_submission(
        "POST", "https://jobs.ashbyhq.com/api/graphql", '{"operationName":"ApiJobPosting"}'
    )


def test_a_strategy_repair_tries_every_candidate_until_one_reads_back(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content('<label for="city">City</label><input id="city">')
    field = find_field(inspect_fields(page), "City")
    assert field is not None
    tried: list[str] = []

    def broken(attempt: Attempt) -> str | None:
        tried.append("broken")
        raise RuntimeError("the widget refused")

    candidate = repair_strategy(
        page, field, "Atlanta", operations={**OPERATIONS, "broken": broken},
        order=("broken", "set_value"),
    )
    assert tried == ["broken"]
    assert candidate is not None
    assert (candidate["kind"], candidate["strategy"], candidate["value"]) == (
        "strategy", "set_value", "Atlanta"
    )
    assert candidate["control_kind"] == "text_entry"
    assert page.input_value("#city") == "Atlanta"


def test_a_format_repair_finds_the_rendering_a_typeahead_accepts(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(TERMS_TYPEAHEAD)
    field = find_field(inspect_fields(page), "Expected graduation")
    knowledge = Knowledge.build(
        facts=[{"key": "education.graduation_date", "value": "2027-12-15", "value_type": "date"}],
        career=None,
    )
    candidate = repair_format(
        page, field, knowledge, "education.graduation_date",  # type: ignore[arg-type]
        failed_formatter="date_as_month_year",
    )
    assert candidate is not None
    assert (candidate["formatter"], candidate["value"]) == ("date_as_term", "Fall 2027")
    assert page.locator(".select__single-value").inner_text() == "Fall 2027"


def test_repair_reports_why_it_could_not(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content('<label for="a">Something else</label><input id="a">')
    knowledge = Knowledge.build(facts=[], career=None)
    issue: dict[str, Any] = {
        "field_label": "Expected graduation", "stopped_at": "format",
        "occurrences": [{"dom_signals": {"question": "Expected graduation",
                                         "fact_key": "education.graduation_date"}}],
    }
    assert repair_on_page(page, issue, knowledge) == (
        None, "the question is not on the first page of the form"
    )
    page.set_content('<label for="s">Submit application</label><input id="s">')
    issue = {"field_label": "Submit application", "stopped_at": "strategy",
             "occurrences": [{"intended_value": "x"}]}
    candidate, note = repair_on_page(page, issue, knowledge)
    assert candidate is None and "refusing" in note


def test_a_validated_strategy_goes_first_in_a_live_fill(tmp_path: Path) -> None:
    from personal_os_worker.jobs.registry.kinds import control_key, preference_key

    field = {"tag": "input", "type": "text"}
    knowledge = Knowledge.build(
        facts=[], career=None,
        preferences=[{"control_kind": "text_entry", "attributes": {"tag": "input", "type": "text"},
                      "strategy": "set_value"}],
    )
    assert knowledge.preferences == {control_key(field): "set_value"}
    assert preference_key("text_entry", {"type": "text", "tag": "input"}) == control_key(field)


def test_a_fill_repairs_in_place_on_any_site_and_stores_the_candidate(  # type: ignore[no-untyped-def]
    page, capsys: pytest.CaptureFixture[str]
) -> None:
    """`apply --repair`: the live page is retried right after the step, whatever the board."""
    from types import SimpleNamespace
    from typing import cast

    from personal_os_worker.jobs.autofill_session import AutofillSession

    page.set_content(TERMS_TYPEAHEAD + '<button type="submit">Submit application</button>')
    updates: list[tuple[str, dict[str, Any]]] = []
    api = SimpleNamespace(update_autofill_failure=lambda issue_id, body: updates.append(
        (issue_id, body)
    ))
    knowledge = Knowledge.build(
        facts=[{"key": "education.graduation_date", "value": "2027-12-15", "value_type": "date"}],
        career=None,
    )
    session = AutofillSession(cast(Any, SimpleNamespace(page=page, api=api, knowledge=knowledge)))
    failures = [
        {"stopped_at": "format", "question": "Expected graduation", "signature": "s1",
         "fact_key": "education.graduation_date", "formatter": "date_as_month_year"},
        # A strategy failure with no value recorded, and one already handled, are left alone.
        {"stopped_at": "strategy", "question": "Expected graduation", "signature": "s2"},
        {"stopped_at": None, "question": "Expected graduation", "signature": "s3"},
    ]
    issues = [{"id": "i1", "signature": "s1"}, {"id": "i2", "signature": "s2"},
              {"id": "i3", "signature": "s3"}]

    session.repair_in_place(inspect_fields(page), failures, issues)

    assert page.locator(".select__single-value").inner_text() == "Fall 2027"
    assert len(updates) == 1
    issue_id, body = updates[0]
    assert issue_id == "i1" and body["status"] == "repaired"
    assert (body["candidate"]["formatter"], body["candidate"]["value"]) == (
        "date_as_term", "Fall 2027"
    )
    printed = " ".join(capsys.readouterr().out.split())
    assert "repaired" in printed and "date_as_term" in printed
    assert "nothing else took" in printed  # the strategy failure had nothing to retry


def test_a_repair_the_api_will_not_record_keeps_its_value_and_the_run(  # type: ignore[no-untyped-def]
    page, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 409 on the issue update warns once; the run and the repaired value carry on."""
    from types import SimpleNamespace
    from typing import cast

    import httpx

    from personal_os_worker.jobs.autofill_session import AutofillSession

    page.set_content(TERMS_TYPEAHEAD + '<button type="submit">Submit application</button>')

    def refuse(issue_id: str, body: dict[str, Any]) -> None:
        request = httpx.Request("PATCH", f"https://api.test/v1/jobs/autofill/failures/{issue_id}")
        raise httpx.HTTPStatusError(
            "conflict", request=request, response=httpx.Response(409, request=request)
        )

    knowledge = Knowledge.build(
        facts=[{"key": "education.graduation_date", "value": "2027-12-15", "value_type": "date"}],
        career=None,
    )
    session = AutofillSession(cast(Any, SimpleNamespace(
        page=page, api=SimpleNamespace(update_autofill_failure=refuse), knowledge=knowledge,
    )))
    failures = [{"stopped_at": "format", "question": "Expected graduation", "signature": "s1",
                 "fact_key": "education.graduation_date", "formatter": "date_as_month_year"}]

    session.repair_in_place(inspect_fields(page), failures, [{"id": "i1", "signature": "s1"}])

    assert page.locator(".select__single-value").inner_text() == "Fall 2027"
    printed = " ".join(capsys.readouterr().out.split())
    assert "Could not record the repair" in printed and "HTTPStatusError" in printed
    assert "repaired" in printed


def test_a_radio_is_repaired_only_with_the_option_that_says_the_value(page) -> None:  # type: ignore[no-untyped-def]
    """A radio's readback is only "checked", so any value would have passed."""
    page.set_content(
        '<fieldset><legend>Can you start within 3 weeks?</legend>'
        '<label><input type="radio" name="start" value="yes">yes</label>'
        '<label><input type="radio" name="start" value="no">no</label></fieldset>'
    )
    field = find_field(inspect_fields(page), "Can you start within 3 weeks?")
    assert field is not None
    assert repair_strategy(page, field, "2") is None
    assert page.locator("input:checked").count() == 0
    candidate = repair_strategy(page, field, "No")
    assert candidate is not None and candidate["value"].casefold() == "no"
    assert page.locator("input[value=no]").is_checked()
