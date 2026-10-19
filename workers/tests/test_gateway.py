"""The gate between a posting and its form: perceived, chosen from a fact, clicked."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.gateway import (
    GATEWAY,
    click_gateway,
    form_appeared,
    inspect_gateways,
)
from personal_os_worker.jobs.registry.kinds import classify
from personal_os_worker.jobs.resolve import Knowledge, Resolver

# Workday's portal: Apply opens a modal of three ways in, and no form until one
# is chosen. Nothing in it is an input, which is why the worker saw an empty page.
WORKDAY_GATE = """
<h1>2027 S/W Embedded Engineering Intern - Annapolis MD</h1>
<button id="apply">Apply</button>
<a href="/login">Sign In</a>
<div id="modal" role="dialog" aria-modal="true" hidden>
  <h2 id="gate-title">Start Your Application</h2>
  <button class="way">Autofill with Resume</button>
  <button class="way">Apply Manually</button>
  <button class="way">Use My Last Application</button>
  <button id="close" aria-label="Close">&times;</button>
</div>
<div id="form"></div>
<script>
// The form is rendered when a way in is chosen, as a real portal does; nothing
// of it exists in the DOM before that.
const FORM = `
  <label for="email">Email Address</label><input id="email" name="email">
  <label for="first">First Name</label><input id="first" name="first">
  <button type="submit">Submit application</button>`;
document.querySelector('#apply').addEventListener('click', () => {
  document.querySelector('#modal').hidden = false;
});
document.querySelectorAll('.way').forEach(button => button.addEventListener('click', () => {
  document.querySelector('#modal').hidden = true;
  document.querySelector('#form').innerHTML = FORM;
}));
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
    page.goto("data:text/html," + quote(WORKDAY_GATE), wait_until="domcontentloaded")
    yield page
    context.close()


def knowledge(**facts: Any) -> Knowledge:
    return Knowledge.build(
        facts=[
            {"key": key, "value": value, "value_type": "text", "sensitive": False}
            for key, value in facts.items()
        ],
        career=None,
        patterns=[],
        prose=[],
        company="Northrop Grumman",
        seed="seed",
    )


def test_a_gate_is_a_choice_group_so_the_registry_already_knows_it() -> None:
    assert classify({"tag": "button", "type": GATEWAY}) == "choice_group"


def test_the_modal_is_captured_as_one_choice_of_three(page) -> None:  # type: ignore[no-untyped-def]
    page.click("#apply")
    gates = inspect_gateways(page)
    assert len(gates) == 1
    gate = gates[0]
    assert gate["label"] == "Start Your Application"
    assert gate["options"] == [
        "Autofill with Resume",
        "Apply Manually",
        "Use My Last Application",
    ]
    assert gate["type"] == GATEWAY


def test_a_page_with_no_gate_open_offers_its_own_controls(page) -> None:  # type: ignore[no-untyped-def]
    # No modal yet: the posting's own Apply is the way in, and Sign In is not.
    gates = inspect_gateways(page)
    assert len(gates) == 1
    assert gates[0]["options"] == ["Apply"]


def test_a_form_page_is_never_a_gate(page) -> None:  # type: ignore[no-untyped-def]
    page.click("#apply")
    page.click("text=Apply Manually")
    assert inspect_gateways(page) == []


def test_the_fact_decides_which_way_in(page) -> None:  # type: ignore[no-untyped-def]
    page.click("#apply")
    gate = inspect_gateways(page)[0]
    resolver = Resolver(knowledge(**{"preferences.application_start": "Autofill with Resume"}))
    # A gate is an ordinary field to the resolver; only an accepted pattern binds it.
    assert resolver.resolve(gate) is None
    assert click_gateway(page, gate, "Autofill with Resume") == "Autofill with Resume"
    assert form_appeared(page, timeout_ms=5_000) is True


def test_a_value_that_names_no_option_clicks_nothing(page) -> None:  # type: ignore[no-untyped-def]
    page.click("#apply")
    gate = inspect_gateways(page)[0]
    assert click_gateway(page, gate, "Upload a video") is None
    assert form_appeared(page, timeout_ms=500) is False


def test_a_bound_question_resolves_the_gate_without_the_model(page) -> None:  # type: ignore[no-untyped-def]
    page.click("#apply")
    gate = inspect_gateways(page)[0]
    bound = Knowledge.build(
        facts=[{
            "key": "preferences.application_start",
            "value": "Autofill with Resume",
            "value_type": "text",
            "sensitive": False,
        }],
        career=None,
        patterns=[{
            "id": "p1",
            "kind": "binding",
            "question_key": "Start Your Application",
            "fact_key": "preferences.application_start",
            "status": "active",
            "resolver_id": "pattern:p1",
        }],
        prose=[],
        company="Northrop Grumman",
        seed="seed",
    )
    resolution = Resolver(bound).resolve(gate)
    assert resolution is not None
    assert resolution.value == "Autofill with Resume"
    assert click_gateway(page, gate, resolution.value) == "Autofill with Resume"
    assert form_appeared(page, timeout_ms=5_000) is True


def test_a_way_in_that_opens_an_account_wall_has_opened(page) -> None:  # type: ignore[no-untyped-def]
    """Workday's Apply Manually leads to Create Account first; the gate did its job."""
    page.click("#apply")
    gate = inspect_gateways(page)[0]
    page.evaluate(
        """() => document.querySelectorAll('.way').forEach(way => way.addEventListener('click',
          () => setTimeout(() => { document.querySelector('#form').innerHTML =
            '<h2>Create Account</h2><label for=e>Email Address</label>'
            + '<input id=e data-automation-id=email type=text>'
            + '<label for=p>Password</label><input id=p type=password>'
            + '<label for=v>Verify New Password</label><input id=v type=password>'
            + '<button data-automation-id=createAccountSubmitButton>Create Account</button>'; }, 0),
          true))"""
    )

    assert click_gateway(page, gate, "Apply Manually") == "Apply Manually"
    assert form_appeared(page, timeout_ms=5_000) is True
