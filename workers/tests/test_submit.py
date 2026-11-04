"""`--submit`: the one press path, every outcome of a press, and the flag's refusals."""

from __future__ import annotations

import email.message
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.cli import main
from personal_os_worker.jobs import apply_commands, apply_run, submit
from personal_os_worker.jobs.audit import Finding
from personal_os_worker.jobs.keys import NoKeys
from personal_os_worker.jobs.mail import verification_code
from personal_os_worker.jobs.page_state import ApplicationPageState
from personal_os_worker.jobs.resolve import Knowledge
from personal_os_worker.jobs.session import AutofillSession
from personal_os_worker.jobs.submit import (
    ApplicationHeld,
    ApplicationParked,
    ApplicationUnconfirmed,
    blocking,
)


@pytest.fixture(scope="module")
def chromium():  # type: ignore[no-untyped-def]
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as error:
            pytest.skip(f"browser launch is restricted: {error}")
        yield browser
        browser.close()


@pytest.fixture
def page(chromium):  # type: ignore[no-untyped-def]
    tab = chromium.new_page()
    yield tab
    tab.close()


class Browser:
    def __init__(self, tab: Any) -> None:
        self.page = tab

    def active_page(self, _current: Any = None) -> Any:
        return self.page


class Fixes:
    """A fix queue that is busy for a number of checks."""

    def __init__(self, busy_for: int = 0, key: str = "control_error|x|y") -> None:
        self.busy_for = busy_for
        self.key = key
        self.served = 0

    def busy(self) -> str | None:
        if self.busy_for > 0:
            self.busy_for -= 1
            return self.key
        return None

    def at_safe_point(self, *_: Any) -> bool:
        self.served += 1
        return False


def knowledge() -> Knowledge:
    return Knowledge.build(
        facts=[{"key": "contact.email", "value": "me@example.com"}],
        career=None, patterns=[], prose=[], company="Acme", seed="s",
    )


def session_on(tab: Any, *, flag: bool = True, fixes: Any = None) -> AutofillSession:
    context = SimpleNamespace(
        page=tab, browser=Browser(tab), link=None, keys=NoKeys(), poll_seconds=0.02,
        submit=flag, fixes=fixes, dry_run=False, knowledge=knowledge(), company="Acme",
        title="Engineer", cdp_url="", console_errors=[],
    )
    session = AutofillSession(cast(Any, context))
    # What the audit and the snapshot do has tests of its own; these count the press.
    session.audits = 0  # type: ignore[attr-defined]
    session.snapshots = 0  # type: ignore[attr-defined]

    def audit(_fields: Any, _result: Any) -> list[Finding]:
        session.audits += 1  # type: ignore[attr-defined]
        return []

    def snapshot(_fields: Any) -> None:
        session.snapshots += 1  # type: ignore[attr-defined]

    session.audit_and_repair = audit  # type: ignore[method-assign,assignment]
    session.record_snapshot = snapshot  # type: ignore[method-assign]
    session.save_now = lambda: 0  # type: ignore[method-assign]
    session.report_review = lambda: None  # type: ignore[method-assign]
    return session


def form(on_press: str, *, label: str = "Submit application", extra: str = "") -> str:
    return f"""
      <form onsubmit="return false">
        <label for="email">Email</label><input id="email" name="email" value="me@example.com">
        {extra}
        <button type="button" id="go" onclick="window.presses=(window.presses||0)+1;{on_press}">
          {label}</button>
      </form>"""


def presses(tab: Any) -> int:
    return int(tab.evaluate("window.presses || 0"))


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(submit, "PRESS_WAIT_SECONDS", 1.0)
    monkeypatch.setattr(submit, "MOVED_SETTLE_SECONDS", 0.1)


CONFIRM = "document.body.innerHTML='<h1>Thank you for applying</h1>'"
NEXT_STEP = (
    "document.body.innerHTML='<form><label for=q>Phone</label><input id=q name=q>"
    "<button type=button>Submit application</button></form>'"
)
ERROR_ONCE = (
    "if(window.presses<3){const e=document.getElementById('email');"
    "e.setAttribute('aria-invalid','true');e.setAttribute('aria-errormessage','m');"
    "document.getElementById('m').textContent='Bad email '+window.presses;}"
    f"else{{{CONFIRM}}}"
)
ERROR_ALWAYS = (
    "const e=document.getElementById('email');"
    "e.setAttribute('aria-invalid','true');e.setAttribute('aria-errormessage','m');"
    "document.getElementById('m').textContent='Bad email '+window.presses;"
)
ERROR_SPAN = '<span id="m"></span>'


def test_a_confirmation_ends_the_step_as_submitted(page: Any) -> None:
    page.set_content(form(CONFIRM))
    session = session_on(page)
    assert session.press_forward([], {}) == ApplicationPageState.SUBMITTED
    assert presses_after_confirm(page) and session.snapshots == 1  # type: ignore[attr-defined]


def presses_after_confirm(tab: Any) -> bool:
    return "Thank you" in tab.locator("body").inner_text()


def test_the_answers_are_saved_before_the_press(page: Any) -> None:
    page.set_content(form(CONFIRM))
    session = session_on(page)
    seen: list[str] = []

    def snapshot(_fields: Any) -> None:
        seen.append(page.locator("body").inner_text())

    session.record_snapshot = snapshot  # type: ignore[method-assign]
    session.press_forward([], {})
    assert seen and "Thank you" not in seen[0]


def test_the_next_step_of_the_form_is_filled_next(page: Any) -> None:
    page.set_content(form(NEXT_STEP, label="Next"))
    session = session_on(page)
    assert session.press_forward([], {}) == ApplicationPageState.FORM


def test_a_validation_error_is_audited_again_and_pressed_again(page: Any) -> None:
    page.set_content(form(ERROR_ONCE, extra=ERROR_SPAN))
    session = session_on(page)
    assert session.press_forward([], {}) == ApplicationPageState.SUBMITTED
    assert session.audits == 3  # type: ignore[attr-defined]


def test_three_presses_is_the_limit_on_a_page_that_keeps_refusing(page: Any) -> None:
    page.set_content(form(ERROR_ALWAYS, extra=ERROR_SPAN))
    session = session_on(page)
    with pytest.raises(ApplicationHeld, match="Bad email"):
        session.press_forward([], {})
    assert page.evaluate("window.presses") == 3


def test_an_emailed_code_is_read_and_entered(
    page: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    show = (
        "if(window.presses===1){document.getElementById('code').style.display='block';}"
        f"else if(document.getElementById('c').value==='482913'){{{CONFIRM}}}"
    )
    page.set_content(form(show, extra=(
        '<div id="code" style="display:none">Enter the security code we sent you'
        '<input id="c" name="verification_code"></div>'
    )))
    message = email.message.EmailMessage()
    message.set_content("Your code is 482913")
    seen: list[str] = []

    class Inbox:
        def await_message(self, **kwargs: Any) -> Any:
            seen.append(str(kwargs["hints"]))
            return message

    monkeypatch.setattr(submit, "open_inbox", lambda _email: Inbox())
    session = session_on(page)
    assert session.press_forward([], {}) == ApplicationPageState.SUBMITTED
    assert seen and page.evaluate("window.presses") == 2


def test_a_code_that_keeps_failing_holds_after_three_tries(
    page: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    show = "document.getElementById('code').style.display='block';"
    page.set_content(form(show, extra=(
        '<div id="code" style="display:none">Enter the security code we sent you'
        '<input id="c" name="verification_code"></div>'
    )))
    message = email.message.EmailMessage()
    message.set_content("Your code is 482913")
    monkeypatch.setattr(
        submit, "open_inbox",
        lambda _email: SimpleNamespace(await_message=lambda **_: message),
    )
    session = session_on(page)
    with pytest.raises(ApplicationHeld, match="code"):
        session.press_forward([], {})
    assert page.evaluate("window.presses") == 4  # the first press and three tries


def test_a_mail_that_never_comes_holds(page: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    show = "document.getElementById('code').style.display='block';"
    page.set_content(form(show, extra=(
        '<div id="code" style="display:none">Enter the security code'
        '<input id="c" name="verification_code"></div>'
    )))
    monkeypatch.setattr(
        submit, "open_inbox", lambda _email: SimpleNamespace(await_message=lambda **_: None)
    )
    with pytest.raises(ApplicationHeld, match="never came"):
        session_on(page).press_forward([], {})


def test_an_image_puzzle_parks_the_run_and_nothing_clicks_it(page: Any) -> None:
    open_puzzle = (
        "const f=document.createElement('iframe');f.title='recaptcha challenge expires';"
        "f.style.width='400px';f.style.height='580px';document.body.appendChild(f);"
    )
    page.set_content(form(open_puzzle))
    with pytest.raises(ApplicationParked, match="puzzle"):
        session_on(page).press_forward([], {})
    assert page.evaluate("window.presses") == 1


def test_a_form_that_does_not_answer_a_next_press_holds(page: Any) -> None:
    page.set_content(form("", label="Next"))
    with pytest.raises(ApplicationHeld, match="did not change"):
        session_on(page).press_forward([], {})
    assert page.evaluate("window.presses") == 1


def test_a_form_that_does_not_answer_a_submit_press_is_never_pressed_again(page: Any) -> None:
    page.set_content(form(""))
    with pytest.raises(ApplicationUnconfirmed, match="did not change"):
        session_on(page).press_forward([], {})
    assert page.evaluate("window.presses") == 1


def test_a_page_that_moved_on_without_a_confirmation_is_never_pressed_again(page: Any) -> None:
    page.set_content(form("document.body.innerHTML='<p>Your profile is great</p>'"))
    with pytest.raises(ApplicationUnconfirmed, match="Your profile"):
        session_on(page).press_forward([], {})
    assert "Your profile" in page.locator("body").inner_text()


def test_the_same_fields_again_after_a_submit_press_are_unconfirmed(page: Any) -> None:
    page.set_content(form("history.pushState({}, '', location.href + '#after');"))
    with pytest.raises(ApplicationUnconfirmed):
        session_on(page).press_forward([], {})


def test_a_confirmation_page_offering_another_application_is_not_pressed(page: Any) -> None:
    page.set_content(form(
        "document.body.innerHTML='<h1>Your application was sent</h1>"
        "<button type=button>Submit another application</button>'"
    ))
    with pytest.raises(ApplicationUnconfirmed):
        session_on(page).press_forward([], {})


@pytest.mark.parametrize(
    "markup",
    [
        '<form><button type="button">Back</button></form>',
        '<form><button type="button">Next</button><button type="button">Submit</button></form>',
        '<form><button type="button" disabled>Submit</button></form>',
    ],
)
def test_no_one_button_to_press_holds(page: Any, markup: str) -> None:
    page.set_content(markup)
    with pytest.raises(ApplicationHeld, match="button"):
        session_on(page).press_forward([], {})


@pytest.mark.parametrize(
    "name", ["Next", "Continue", "Save and Continue", "Submit", "Submit Application",
             "Send Application", "Finish application", "Review and submit"],
)
def test_the_words_of_a_press(page: Any, name: str) -> None:
    page.set_content(f'<form><button type="button" id="b">{name}</button></form>')
    assert session_on(page).press_target()[1]


@pytest.mark.parametrize(
    "name", ["Next month", "Continue with Google", "Submit another application", "Back", "Save"],
)
def test_what_is_never_a_press(page: Any, name: str) -> None:
    page.set_content(f'<form><button type="button">{name}</button></form>')
    with pytest.raises(ApplicationHeld):
        session_on(page).press_target()


def test_the_press_waits_for_the_fixer_and_serves_its_safe_points(page: Any) -> None:
    page.set_content(form(CONFIRM))
    fixes = Fixes(busy_for=3)
    session = session_on(page, fixes=fixes)
    session.safe_point = lambda: setattr(  # type: ignore[method-assign]
        fixes, "served", fixes.served + 1
    )
    assert session.press_forward([], {}) == ApplicationPageState.SUBMITTED
    assert fixes.served >= 3


def test_a_problem_the_fixer_keeps_past_the_limit_holds(
    page: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(submit, "FIXER_WAIT_SECONDS", 0.05)
    page.set_content(form(CONFIRM))
    session = session_on(page, fixes=Fixes(busy_for=10_000))
    with pytest.raises(ApplicationHeld, match="fixer"):
        session.press_forward([], {})
    assert presses(page) == 0


def test_an_open_slot_in_the_letter_holds(page: Any) -> None:
    page.set_content(form(CONFIRM))
    session = session_on(page)
    session.state.cover_letter = ""
    with pytest.raises(ApplicationHeld, match="letter"):
        session.press_forward([], {})
    assert presses(page) == 0


def test_a_choice_a_fact_contradicts_holds(page: Any) -> None:
    page.set_content(form(CONFIRM))
    session = session_on(page)
    session.state.withheld["0:x"] = "No"
    with pytest.raises(ApplicationHeld, match="contradicts"):
        session.press_forward([], {})
    assert presses(page) == 0


def finding(cause: str, *, required: bool = True, **extra: Any) -> Finding:
    return Finding("Q", cause, required, "why", **extra)  # type: ignore[arg-type]


FIELD = [{"label": "Q", "type": "text", "tag": "input"}]
BOX = [{"label": "Q", "type": "textarea", "tag": "textarea"}]


@pytest.mark.parametrize(
    "cause", ["wrong_answer", "control_error", "format_error", "unknown_control",
              "detection_error", "model_error", "draft_error"],
)
def test_what_holds_whether_or_not_it_is_required(cause: str) -> None:
    assert blocking([finding(cause, required=False)], FIELD)


@pytest.mark.parametrize("cause", ["fact_gap", "pattern_gap"])
def test_a_required_field_with_no_fact_holds(cause: str) -> None:
    assert blocking([finding(cause)], FIELD)


@pytest.mark.parametrize("cause", ["fact_gap", "pattern_gap"])
def test_an_optional_short_question_with_no_fact_does_not(cause: str) -> None:
    assert not blocking([finding(cause, required=False)], FIELD)


def test_an_optional_long_form_or_upload_with_nothing_in_it_holds() -> None:
    assert blocking([finding("fact_gap", required=False)], BOX)
    upload = [{"label": "Q", "type": "file", "tag": "input"}]
    assert blocking([finding("pattern_gap", required=False)], upload)


def test_a_page_error_alone_waits_for_the_press_limit() -> None:
    assert not blocking([finding("control_error", evidence={"page_error": "x"})], FIELD)


def test_an_optional_widget_with_no_question_cannot_hold_the_application() -> None:
    assert not blocking(
        [finding("unknown_control", required=False, evidence={"unlabelled": True})], FIELD
    )
    assert blocking([finding("unknown_control", evidence={"unlabelled": True})], FIELD)


def test_without_the_flag_nothing_is_ever_pressed(
    page: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    page.set_content(form(CONFIRM))
    session = session_on(page, flag=False)
    waits = {"count": 0}

    def wait(_seconds: float) -> set[str]:
        waits["count"] += 1
        if waits["count"] >= 5:
            page.evaluate("document.body.innerHTML='<h1>Thank you for applying</h1>'")
        return set()

    cast(Any, session.ctx).keys = SimpleNamespace(wait=wait)
    assert session.wait_and_advance([]) == ApplicationPageState.SUBMITTED
    assert presses(page) == 0
    assert "press_forward" not in session.wait_and_advance.__code__.co_names


def test_a_wall_holds_once_nothing_is_working_on_it(
    page: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(submit, "WALL_WAIT_SECONDS", 0.0)
    page.set_content("<p>Sorry</p>")
    session = session_on(page)
    session.state.walls.append(Finding("Reach the application form", "wall", True, "no Apply"))
    session.stalled()
    with pytest.raises(ApplicationHeld, match="no Apply"):
        session.stalled()


def test_a_wall_waits_while_the_fixer_works(page: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(submit, "WALL_WAIT_SECONDS", 0.0)
    page.set_content("<p>Sorry</p>")
    session = session_on(page, fixes=Fixes(busy_for=100))
    session.stalled()
    session.stalled()


def test_a_wall_is_never_stopped_without_the_flag(page: Any) -> None:
    page.set_content("<p>Sorry</p>")
    session = session_on(page, flag=False)
    session.stalled()
    session.stalled()


def test_a_held_run_ends_abandoned_and_closes_its_tab() -> None:
    calls: list[tuple[str, str]] = []
    tab = SimpleNamespace(is_closed=lambda: False, close=lambda: calls.append(("close", "")))
    api = SimpleNamespace(stop_autofill_run=lambda run, status: calls.append((run, status)))
    result = apply_run.finalize_application_outcome(
        api, run={"company_name": "Acme"}, run_id="r1",  # type: ignore[arg-type]
        outcome={"held": "no fact", "answers": []}, page=tab,
    )
    assert result == "held" and calls == [("r1", "abandoned"), ("close", "")]


@pytest.mark.parametrize("kind", ["parked", "unconfirmed"])
def test_a_parked_or_unconfirmed_run_keeps_its_tab(kind: str) -> None:
    calls: list[tuple[str, str]] = []
    tab = SimpleNamespace(is_closed=lambda: False, close=lambda: calls.append(("close", "")))
    api = SimpleNamespace(stop_autofill_run=lambda run, status: calls.append((run, status)))
    result = apply_run.finalize_application_outcome(
        api, run={"company_name": "Acme"}, run_id="r1",  # type: ignore[arg-type]
        outcome={kind: "why", "answers": []}, page=tab,
    )
    assert result == kind and calls == [("r1", "abandoned")]


@pytest.mark.parametrize(
    ("result", "code"),
    [("submitted", 0), ("held", 10), ("parked", 11), ("unconfirmed", 12), ("skipped", 13),
     ("stopped", 14)],
)
def test_each_ending_has_its_own_exit_code(
    monkeypatch: pytest.MonkeyPatch, result: str, code: int
) -> None:
    monkeypatch.setattr(apply_commands, "connected", lambda: (None, ""))
    monkeypatch.setattr(
        apply_commands, "WorkerApi", lambda *_: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setattr(apply_commands, "listing_for", lambda *_a, **_k: {"id": "1"})
    monkeypatch.setattr(apply_commands, "apply_posting", lambda *_a, **_k: result)
    assert apply_commands.run_autofill("7", dry_run=False, submit=True) == code


def test_without_the_flag_every_ending_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply_commands, "connected", lambda: (None, ""))
    monkeypatch.setattr(
        apply_commands, "WorkerApi", lambda *_: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setattr(apply_commands, "listing_for", lambda *_a, **_k: {"id": "1"})
    monkeypatch.setattr(apply_commands, "apply_posting", lambda *_a, **_k: "stopped")
    assert apply_commands.run_autofill("7", dry_run=False) == 0


@pytest.mark.parametrize(
    ("flags", "words"),
    [
        (["--dry-run"], "--submit"),
        (["--no-claude"], "--submit"),
        (["--remote"], "--submit"),
        (["--done"], "--done takes no other flags"),
    ],
)
def test_the_flag_refuses_what_would_submit_without_an_audit(
    flags: list[str], words: str
) -> None:
    with pytest.raises(SystemExit, match=words):
        main(["hunt", "apply", "7", "--submit", *flags])


def test_the_flag_implies_accepting_the_letter(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def run(reference: str, **flags: Any) -> int:
        seen.update(flags, reference=reference)
        return 0

    monkeypatch.setattr(apply_commands, "run_autofill", run)
    assert main(["hunt", "apply", "7", "--submit"]) == 0
    assert seen["submit"] is True and seen["accept_letter"] is True


def test_the_device_agent_never_passes_the_flag() -> None:
    from personal_os_worker.device.runs import apply_command

    assert "--submit" not in apply_command("7", fresh=False)


def test_a_submit_run_keeps_the_letter_off_the_hunt_page() -> None:
    linked = apply_run._linked(None, accept_letter=True, submit=True)
    assert linked["editor"].keywords == {"link": None, "accept": True}


def test_a_mailed_code_may_mix_letters_and_digits() -> None:
    message = email.message.EmailMessage()
    message.set_content("Copy and paste this code into the security code field: aB3dE9xZ")
    assert verification_code(message) == "aB3dE9xZ"
    plain = email.message.EmailMessage()
    plain.set_content("Your code is 482913 for Acme")
    assert verification_code(plain) == "482913"


def test_the_press_is_outside_the_guards(page: Any) -> None:
    from personal_os_worker.jobs.safety import armed

    page.set_content(form(CONFIRM))
    with armed(page):
        pass  # the guard script is now on the page, disarmed, as on any fill
    assert session_on(page).press_forward([], {}) == ApplicationPageState.SUBMITTED


def test_docs_name_the_one_exception() -> None:
    from personal_os_worker.jobs import safety

    assert "--submit" in (safety.__doc__ or "")
    readme = (Path(__file__).parents[1] / "README.md").read_text()
    assert "--submit" in readme
