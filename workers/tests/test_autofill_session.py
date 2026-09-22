from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.autofill_session import AutofillSession, SessionContext
from personal_os_worker.browser import BrowserClosedError, VisibleBrowser, inspect_fields
from personal_os_worker.page_state import ApplicationPageState, classify_application_page

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def browser_page():  # type: ignore[no-untyped-def]
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as error:
            pytest.skip(f"browser launch is restricted: {error}")
        context = browser.new_context()
        page = context.new_page()
        yield page
        context.close()
        browser.close()


class FakeApi:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.artifacts: list[str] = []
        self.library: dict[str, dict[str, Any]] = {}

    def create_answer(self, **item: Any) -> dict[str, Any]:
        self.library[str(item["question"])] = item
        return item

    def persist_autofill_step(self, run_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self.steps.append(payload)
        return []

    def upload_autofill_artifact(self, run_id: str, *, name: str, **_: Any) -> str:
        self.artifacts.append(name)
        return f"artifacts/{name}"


def test_save_now_does_not_approve_collected_model_answer() -> None:
    api = FakeApi()
    context = SimpleNamespace(api=api, run_id="run-test", page=SimpleNamespace(url="https://example.test"))
    session = AutofillSession(cast(Any, context))
    session.state.collected["Why this company?"] = {
        "question": "Why this company?",
        "answer_text": "Tailored draft",
        "source": "model",
        "reuse_policy": "contextual",
    }
    session.state.observations.append({"event_index": 0, "question": "Why this company?"})

    session.save_now()
    session.save_now()

    assert api.library == {}
    assert len(api.steps) == 1
    assert api.steps[0]["observations"] == [
        {"event_index": 0, "question": "Why this company?"}
    ]
    assert any(item["answer_text"] == "Tailored draft" for item in session.state.collected.values())


def test_save_now_sends_large_history_in_bounded_checkpoints() -> None:
    api = FakeApi()
    context = SimpleNamespace(api=api, run_id="run-test", page=SimpleNamespace(url="https://example.test"))
    session = AutofillSession(cast(Any, context))
    session.state.observations = [{"event_index": index} for index in range(501)]

    assert session.save_now() == 501

    assert [len(step["observations"]) for step in api.steps] == [500, 1]


def test_repeated_question_on_two_controls_keeps_both_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = state_machine_session()
    session.ctx.answers = []
    session.state.step = 2
    entries = [
        {"question": "Location", "answer_text": "Atlanta", "control_kind": "text",
         "field_fingerprint": "location-primary"},
        {"question": "Location", "answer_text": "Remote", "control_kind": "text",
         "field_fingerprint": "location-secondary"},
    ]
    monkeypatch.setattr(
        "personal_os_worker.autofill_session.snapshot_entries",
        lambda *_: entries,
    )

    session.record_snapshot([])

    assert {item["answer_text"] for item in session.state.collected.values()} == {
        "Atlanta", "Remote"
    }


def test_cleared_control_is_removed_from_final_answers_and_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = state_machine_session()
    session.ctx.answers = []
    snapshots = iter([
        [{"question": "Location", "answer_text": "Atlanta", "control_kind": "text",
          "field_fingerprint": "location#1"}],
        [{"question": "Location", "answer_text": "", "control_kind": "text",
          "field_fingerprint": "location#1"}],
    ])
    monkeypatch.setattr(
        "personal_os_worker.autofill_session.snapshot_entries", lambda *_: next(snapshots)
    )

    fields = [{"fingerprint": "location", "index": 1}]
    session.record_snapshot(fields)
    session.record_snapshot(fields)

    assert session.state.collected == {}
    assert [event["answer_text"] for event in session.state.observations] == ["Atlanta", ""]


def test_failed_control_read_preserves_last_known_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = state_machine_session()
    session.ctx.answers = []
    snapshots = iter([
        [{"question": "Location", "answer_text": "Atlanta", "control_kind": "text",
          "field_fingerprint": "location#1"}],
        [],  # The reader omitted a control it could not read.
    ])
    monkeypatch.setattr(
        "personal_os_worker.autofill_session.snapshot_entries", lambda *_: next(snapshots)
    )
    fields = [{"fingerprint": "location", "index": 1}]
    session.record_snapshot(fields)
    session.record_snapshot(fields)

    assert session.answers_so_far()[0]["answer_text"] == "Atlanta"
    assert len(session.state.observations) == 1


def test_edit_retains_resolver_responsible_for_initial_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = state_machine_session()
    session.ctx.answers = []
    session.state.tier_answers["Location"] = "Atlanta"
    session.state.tier_source["Location"] = "pattern"
    session.state.tier_resolver["Location"] = "builtin:location"
    snapshots = iter([
        [{"question": "Location", "answer_text": value, "control_kind": "text",
          "field_fingerprint": "location#1"}]
        for value in ("Atlanta", "Remote")
    ])
    monkeypatch.setattr(
        "personal_os_worker.autofill_session.snapshot_entries", lambda *_: next(snapshots)
    )
    session.record_snapshot([])
    session.record_snapshot([])

    edited = session.state.observations[-1]
    assert edited["source"] == "manual"
    assert edited["resolver_id"] == "builtin:location"
    assert edited["proposed_value"] == "Atlanta"


class ScriptedKeys:
    """Returns one scripted key set per wait, then nothing."""

    def __init__(self, script: list[set[str]]) -> None:
        self.script = list(script)

    def __enter__(self) -> ScriptedKeys:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def wait(self, seconds: float) -> set[str]:
        return self.script.pop(0) if self.script else set()


class InterruptingKeys(ScriptedKeys):
    """Raises KeyboardInterrupt on the first wait after the script runs out."""

    def wait(self, seconds: float) -> set[str]:
        if not self.script:
            raise KeyboardInterrupt
        return super().wait(seconds)


class FakeBrowser:
    def __init__(self, page: Any) -> None:
        self.page = page

    def active_page(self, _current_page: Any | None = None) -> Any:
        return self.page


def state_machine_session() -> AutofillSession:
    page = SimpleNamespace(url="https://jobs.ashbyhq.com/acme/role/application")
    context = SimpleNamespace(page=page, browser=FakeBrowser(page))
    return AutofillSession(cast(Any, context))


def test_overview_return_stays_in_the_same_wait_step(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = state_machine_session()
    fields = [{"fingerprint": "email"}]
    states = iter(
        [ApplicationPageState.OVERVIEW, ApplicationPageState.FORM, ApplicationPageState.SUBMITTED]
    )
    monkeypatch.setattr("personal_os_worker.autofill_session.inspect_fields", lambda _: fields)
    monkeypatch.setattr(
        "personal_os_worker.autofill_session.classify_application_page",
        lambda *_: next(states),
    )
    monkeypatch.setattr("personal_os_worker.autofill_session.has_submit_control", lambda _: False)
    session.sleep = lambda: None  # type: ignore[method-assign]
    session.record_snapshot = lambda _: None  # type: ignore[method-assign]

    assert session.wait_and_advance(fields) == ApplicationPageState.SUBMITTED
    assert capsys.readouterr().out.count("standing by") == 1


def test_temporary_empty_application_rerender_does_not_report_a_wall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = state_machine_session()
    fields = [{"fingerprint": "email"}]
    states = iter(
        [ApplicationPageState.LOADING, ApplicationPageState.FORM, ApplicationPageState.SUBMITTED]
    )
    failures: list[str] = []
    monkeypatch.setattr("personal_os_worker.autofill_session.inspect_fields", lambda _: fields)
    monkeypatch.setattr(
        "personal_os_worker.autofill_session.classify_application_page",
        lambda *_: next(states),
    )
    monkeypatch.setattr("personal_os_worker.autofill_session.has_submit_control", lambda _: False)
    monkeypatch.setattr("personal_os_worker.autofill_session.time.monotonic", lambda: 1.0)
    session.sleep = lambda: None  # type: ignore[method-assign]
    session.record_snapshot = lambda _: None  # type: ignore[method-assign]
    session.report_page_failure = (  # type: ignore[method-assign]
        lambda category, message: failures.append(category)
    )

    assert session.wait_and_advance(fields) == ApplicationPageState.SUBMITTED
    assert failures == []


def test_unrelated_foreground_tab_stands_by_until_the_form_returns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Switching to another tab makes that tab the active page; the session must
    # wait there rather than end, and the same step resumes when the form is back.
    session = state_machine_session()
    fields = [{"fingerprint": "email"}]
    states = iter(
        [
            ApplicationPageState.LOADING,
            ApplicationPageState.UNKNOWN,
            ApplicationPageState.UNKNOWN,
            ApplicationPageState.FORM,
            ApplicationPageState.SUBMITTED,
        ]
    )
    times = iter([1.0, 1.0, 7.0, 8.0, 9.0, 10.0])
    failures: list[str] = []
    monkeypatch.setattr("personal_os_worker.autofill_session.inspect_fields", lambda _: fields)
    monkeypatch.setattr(
        "personal_os_worker.autofill_session.classify_application_page",
        lambda *_: next(states),
    )
    monkeypatch.setattr("personal_os_worker.autofill_session.has_submit_control", lambda _: False)
    monkeypatch.setattr("personal_os_worker.autofill_session.time.monotonic", lambda: next(times))
    session.sleep = lambda: None  # type: ignore[method-assign]
    session.record_snapshot = lambda _: None  # type: ignore[method-assign]
    session.report_page_failure = (  # type: ignore[method-assign]
        lambda category, message: failures.append(category)
    )

    assert session.wait_and_advance(fields) == ApplicationPageState.SUBMITTED
    assert failures == []
    assert capsys.readouterr().out.count("standing by") == 1
def test_looks_submitted_recognizes_ashbys_success_container_regardless_of_wording(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    # OpenGov's Ashby posting confirms with "was successfully submitted", which
    # slips past every SUBMITTED_MARKERS phrase (none matches with "successfully"
    # inserted); Ashby's own success container class does not depend on the
    # company's chosen copy.
    browser_page.set_content(
        '<div class="ashby-application-form-success-container">'
        "<p>Your application was successfully submitted. "
        "We'll contact you if there are next steps.</p></div>"
    )

    assert classify_application_page(browser_page, []) == ApplicationPageState.SUBMITTED


def test_visible_submit_bypasses_the_application_form_heuristic(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(
        """
        <label for="email">Email</label><input id="email" type="email">
        <button type="submit">Submit application</button>
        """
    )
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-submit",
        adapter_name="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        profile={},
        answers=[],
        scoped=[],
        resume_json={},
        resume_pdf=tmp_path / "resume.pdf",
        company="Acme",
        title="Engineer",
        description=None,
    )

    fields = AutofillSession(context).wait_for_form()

    assert fields is not None
    assert "Hit a wall" not in capsys.readouterr().out


def test_active_page_rejects_a_closed_session_page_when_another_web_tab_remains(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.goto("data:text/html,<title>Other tab</title>")
    application_page = browser_page.context.new_page()
    application_page.goto("data:text/html,<title>Application</title>")
    application_page.close()
    browser = VisibleBrowser("http://unused")
    browser._browser = cast(Any, browser_page.context.browser)

    with pytest.raises(BrowserClosedError):
        browser.active_page(application_page)


def test_session_stops_when_page_closes_during_form_inspection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SimpleNamespace(
        url="https://boards.example/jobs/123",
        is_closed=lambda: True,
    )
    browser = SimpleNamespace(active_page=lambda _current=None: page)

    def closed_inspection(_: Any) -> list[dict[str, Any]]:
        raise Error("Target page, context or browser has been closed")

    monkeypatch.setattr(
        "personal_os_worker.autofill_session.reveal_application_form", lambda _: False
    )
    monkeypatch.setattr("personal_os_worker.autofill_session.inspect_fields", closed_inspection)
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-race",
        adapter_name="greenhouse",
        browser=cast(Any, browser),
        page=cast(Any, page),
        console_errors=[],
        profile={},
        answers=[],
        scoped=[],
        resume_json={},
        resume_pdf=tmp_path / "missing.pdf",
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
    )

    outcome = AutofillSession(context).run()

    assert "browser or application tab was closed" in capsys.readouterr().out.casefold()
    assert outcome == {"final_url": page.url, "answers": [], "submitted": False}


def test_session_reraises_unrelated_playwright_error_that_mentions_target_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SimpleNamespace(
        url="https://boards.example/jobs/123",
        is_closed=lambda: False,
    )
    browser = SimpleNamespace(active_page=lambda _current=None: page)

    def failed_inspection(_: Any) -> list[dict[str, Any]]:
        raise Error('Locator.click: Timeout waiting for get_by_text("Target closed")')

    monkeypatch.setattr(
        "personal_os_worker.autofill_session.reveal_application_form", lambda _: False
    )
    monkeypatch.setattr("personal_os_worker.autofill_session.inspect_fields", failed_inspection)
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-error",
        adapter_name="greenhouse",
        browser=cast(Any, browser),
        page=cast(Any, page),
        console_errors=[],
        profile={},
        answers=[],
        scoped=[],
        resume_json={},
        resume_pdf=tmp_path / "missing.pdf",
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
    )

    with pytest.raises(Error, match="Target closed"):
        AutofillSession(context).run()


@pytest.mark.parametrize(
    "contexts",
    [[], [SimpleNamespace(pages=[])]],
    ids=["browser-closed", "application-tab-closed"],
)
def test_session_stops_cleanly_when_browser_or_application_tab_closes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    contexts: list[Any],
) -> None:
    page = SimpleNamespace(
        url="https://boards.example/jobs/123",
        is_closed=lambda: False,
    )
    monkeypatch.setattr(
        "personal_os_worker.autofill_session.reveal_application_form", lambda _: False
    )
    browser = VisibleBrowser("http://unused")
    browser._browser = cast(Any, SimpleNamespace(contexts=contexts))
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-closed",
        adapter_name="greenhouse",
        browser=browser,
        page=cast(Any, page),
        console_errors=[],
        profile={},
        answers=[],
        scoped=[],
        resume_json={},
        resume_pdf=tmp_path / "missing.pdf",
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
    )

    outcome = AutofillSession(context).run()

    assert "browser or application tab was closed" in capsys.readouterr().out.casefold()
    assert outcome == {"final_url": page.url, "answers": [], "submitted": False}


def test_ctrl_d_skips_the_listing_and_still_saves_what_was_picked_up(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content((FIXTURES / "greenhouse.html").read_text())
    browser_page.fill("#why", "Half an answer.")
    api = FakeApi()
    context = SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-skip",
        adapter_name="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        profile={"legal_name": "Alex Example", "email": "ethan@example.com"},
        answers=[],
        scoped=[],
        resume_json={},
        resume_pdf=tmp_path / "main.pdf",
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        drafter=lambda **_: None,
        use_claude=False,
        poll_seconds=0.01,
        keys=ScriptedKeys([set(), {"skip"}]),
    )
    (tmp_path / "main.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    outcome = AutofillSession(context).run()
    assert outcome["skipped"] is True and "submitted" not in outcome
    assert api.library == {}
    assert any(item["answer_text"] == "Half an answer." for item in outcome["answers"])


def test_cover_letter_is_drafted_once_edited_in_the_editor_then_filled_and_uploaded(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from personal_os_worker.cover_letter import LetterSlots, load_template

    browser_page.set_content(
        """
        <form>
          <label for="first">First name</label><input id="first" name="first_name" required>
          <label for="cl">Cover Letter</label><textarea id="cl" name="cover_letter"></textarea>
          <label for="why">Why us?</label><textarea id="why" required></textarea>
          <label for="clf">Cover letter (PDF)</label>
          <input id="clf" name="cover_letter_file" type="file">
        </form>
        """
    )
    template_file = tmp_path / "cover_letter.md"
    template_file.write_text(
        "## Letter\n\nDear {{hiring_manager}},\n\n{{hook}} {{proof_primary}} {{proof_secondary}}"
        "\n\n{{tie}} {{why_this}}\n\n## Modules\n\n### one\n\nProof one.\n"
    )
    drafts: list[dict[str, Any]] = []
    edited: list[Path] = []
    rendered: list[str] = []

    def letter_drafter(template: Any, *, posting: dict[str, Any], resume: Any) -> LetterSlots:
        drafts.append(posting)
        return LetterSlots("", "Hook.", "w", "one", None, "Tie.")

    def editor(path: Path) -> None:
        edited.append(path)
        path.write_text(path.read_text().replace("Hook.", "Edited hook."))

    def renderer(contact: Any, text: str, directory: Path, *, company: str) -> Path:
        rendered.append(text)
        pdf = directory / "Alex_Example_Cover_Letter.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")
        return pdf

    api = FakeApi()
    context = SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-letter",
        adapter_name="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        profile={"legal_name": "Alex Example"},
        answers=[],
        scoped=[],
        resume_json={"contact": {"name": "Alex Example"}},
        resume_pdf=tmp_path / "main.pdf",
        company="Acme",
        title="SWE Intern",
        description="We build things.",
        drafter=lambda **_: "Ordinary draft.",
        letter_drafter=letter_drafter,
        letter_template=lambda: load_template(template_file),
        editor=editor,
        letter_renderer=renderer,
        letter_dir=tmp_path / "letters",
        use_claude=True,
        short_drafter=lambda questions, **_: {},
        poll_seconds=0.01,
        keys=ScriptedKeys([set(), {"done"}]),
    )
    (tmp_path / "main.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    outcome = AutofillSession(context).run()

    assert drafts == [{"company": "Acme", "role": "SWE Intern", "description": "We build things."}]
    assert len(edited) == 1 and edited[0].name == "cover_letter.md"
    letter = edited[0].read_text()
    assert letter.startswith("Dear Hiring Team,\n\nEdited hook. Proof one.\n\nTie. w\n")
    assert browser_page.input_value("#cl") == letter.strip() + "\n"
    assert browser_page.input_value("#why") == "Ordinary draft."  # untouched by the letter
    assert rendered == [letter.strip() + "\n"]
    assert browser_page.evaluate("document.querySelector('#clf').files.length") == 1
    saved = {item["question"]: item for item in outcome["answers"]}
    assert saved["Cover Letter"]["source"] == "model"
    assert saved["Cover Letter"]["reuse_policy"] == "contextual"
    printed = capsys.readouterr().out
    assert "Cover letter open in VS Code" in printed
    assert printed.count("drafting the cover letter slots") == 1


def test_session_seeds_long_form_records_every_answer_and_ends_on_submit(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content((FIXTURES / "greenhouse.html").read_text())
    api = FakeApi()
    drafted: list[str] = []

    def drafter(**kwargs: Any) -> str:
        drafted.append(str(kwargs["question"]))
        return "Claude's tailored draft."

    resume = tmp_path / "main.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    context = SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-1",
        adapter_name="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        profile={"legal_name": "Alex Example", "email": "ethan@example.com"},
        answers=[
            {
                "question": "Why this team specifically?",
                "normalized_key": "why this team specifically",
                "answer_text": "Similar, not exact.",
                "reuse_policy": "exact",
            }
        ],
        scoped=[],
        resume_json={"contact": {"name": "Ethan"}},
        resume_pdf=resume,
        company="Acme",
        title="Backend Engineer Intern",
        description="Build things.",
        drafter=drafter,
        short_drafter=lambda *_args, **_kwargs: {},
        use_claude=True,
        poll_seconds=0.05,
        keys=ScriptedKeys([set(), set(), {"save"}]),
    )
    session = AutofillSession(context)
    polls = {"count": 0}
    seen: dict[str, str] = {}
    original_sleep = session.sleep

    def sleep_then_submit() -> None:
        # The user picks a country, then presses Submit; the confirmation page appears.
        polls["count"] += 1
        if polls["count"] == 2:
            seen["why"] = browser_page.input_value("#why")
            browser_page.select_option("#country", label="Canada")
        if polls["count"] == 4:
            browser_page.set_content("<h1>Thank you for applying to Acme!</h1>")
        original_sleep()

    session.sleep = sleep_then_submit  # type: ignore[method-assign]

    outcome = session.run()

    assert drafted == ["Why this team?"]
    assert seen["why"] == "Claude's tailored draft."
    saved = {item["question"]: item for item in outcome["answers"]}
    assert saved["Why this team?"]["answer_text"] == "Claude's tailored draft."
    assert saved["Why this team?"]["source"] == "model"
    assert saved["Why this team?"]["resolver_id"] == "model:long_form_v1"
    assert any(result.resolver_id == "model:long_form_v1"
               for result in session.state.resolutions.values())
    assert saved["Why this team?"]["reuse_policy"] == "contextual"
    assert saved["Country"]["answer_text"] == "Canada"
    assert saved["Country"]["source"] == "manual"
    assert saved["Country"]["reuse_policy"] == "exact"
    assert saved["Country"]["control_kind"] == "select"
    assert saved["First name"]["source"] == "jobs_profile"
    assert saved["Email"]["answer_text"] == "ethan@example.com"
    assert "dry_run" not in outcome
    assert outcome["submitted"] is True
    printed = capsys.readouterr().out
    assert "long-form  Why this team?" in printed and "(claude draft)" in printed
    assert "Review before submitting" in printed
    assert "picked up  Country" in printed and "-> Canada" in printed
    # Ctrl+S keeps the current values in the run without approving reuse.
    assert api.library == {}
    assert "answer observation(s) for review" in printed
    assert printed.count("picked up  Country") == 1
    assert "picked up  Why this team?" not in printed  # the seed already reported it
    assert "picked up: First name" not in printed  # the worker filled it


def test_session_resolves_pattern_and_claude_tiers_for_unfilled_fields(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(
        """
        <form>
          <label for="resume">Resume</label><input id="resume" type="file">
          <label for="first">First name</label><input id="first" name="first_name" required>
          <fieldset><legend>Consider me for other roles?</legend>
            <label><input type="radio" name="other_roles" value="Yes">Yes</label>
            <label><input type="radio" name="other_roles" value="No">No</label>
          </fieldset>
          <fieldset><legend>Have you ever worked for Acme?</legend>
            <label>
              <input type="radio" name="worked" value="Yes - previous intern">Yes - previous intern
            </label>
            <label><input type="radio" name="worked" value="No - never">No - never</label>
          </fieldset>
          <label for="school">What school are you attending?</label>
          <input id="school" required>
          <button type="submit">Submit</button>
        </form>
        """
    )
    api = FakeApi()
    asked: list[str] = []

    def short_drafter(questions: Any, **_: Any) -> dict[str, str]:
        asked.extend(str(question.question) for question in questions)
        return {"What school are you attending?": "Example Institute of Technology"}

    resume_pdf = tmp_path / "main.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    context = SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-tiers",
        adapter_name="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        profile={"legal_name": "Alex Example"},
        answers=[
            {
                "reuse_policy": "pattern",
                "normalized_question": "consider me for other",
                "answer_text": "Yes",
            }
        ],
        scoped=[],
        resume_json={},
        resume_pdf=resume_pdf,
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        short_drafter=short_drafter,
        poll_seconds=0.05,
        keys=ScriptedKeys([{"save"}]),
    )
    session = AutofillSession(context)
    polls = {"count": 0}
    original_sleep = session.sleep

    def sleep_then_submit() -> None:
        polls["count"] += 1
        if polls["count"] == 2:
            browser_page.set_content("<h1>Thank you for applying to Acme!</h1>")
        original_sleep()

    session.sleep = sleep_then_submit  # type: ignore[method-assign]

    outcome = session.run()

    assert asked == ["What school are you attending?"]
    saved = {item["question"]: item for item in outcome["answers"]}
    assert saved["Consider me for other roles?"]["answer_text"] == "Yes"
    assert saved["Consider me for other roles?"]["source"] == "pattern"
    assert saved["Have you ever worked for Acme?"]["answer_text"] == "No - never"
    assert saved["Have you ever worked for Acme?"]["source"] == "pattern"
    assert saved["What school are you attending?"]["answer_text"] == (
        "Example Institute of Technology"
    )
    assert saved["What school are you attending?"]["source"] == "model"
    printed = capsys.readouterr().out
    assert "pattern    Consider me for other roles?" in printed and "-> Yes" in printed
    assert "pattern    Have you ever worked for Acme?" in printed and "-> No - never" in printed
    assert "claude     What school are you attending?" in printed
    assert api.library == {}


def test_dry_run_plans_without_opening_tabs_or_writing(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content((FIXTURES / "greenhouse.html").read_text())
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-2",
        adapter_name="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        profile={"legal_name": "Alex Example"},
        answers=[],
        scoped=[],
        resume_json={},
        resume_pdf=tmp_path / "missing.pdf",
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        dry_run=True,
        drafter=lambda **_: pytest.fail("dry run must not call Claude"),
    )
    outcome = AutofillSession(context).run()
    assert outcome["dry_run"] is True
    assert browser_page.locator("#first").input_value() == ""
    assert not (tmp_path / "drafts").exists()


def test_ctrl_c_ends_cleanly_with_the_answers_picked_up_so_far(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content((FIXTURES / "greenhouse.html").read_text())
    browser_page.fill("#why", "Typed on the page.")
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-3",
        adapter_name="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        profile={"legal_name": "Alex Example", "email": "ethan@example.com"},
        answers=[],
        scoped=[],
        resume_json={},
        resume_pdf=tmp_path / "main.pdf",
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        drafter=lambda **_: None,
        use_claude=False,
        poll_seconds=0.01,
        keys=InterruptingKeys([set()]),
    )
    (tmp_path / "main.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    outcome = AutofillSession(context).run()
    assert outcome["interrupted"] is True
    saved = {item["question"]: item["answer_text"] for item in outcome["answers"]}
    assert saved["Why this team?"] == "Typed on the page."
    assert saved["First name"] == "Ethan"


def test_claude_is_asked_about_a_question_once_per_run(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content(
        """
        <form>
          <label for="resume">Resume</label><input id="resume" type="file">
          <label for="first">First name</label><input id="first" name="first_name" required>
          <label for="why">Why us, in a word?</label><input id="why" required>
        </form>
        """
    )
    calls: list[list[str]] = []

    def short_drafter(questions: Any, **_: Any) -> dict[str, str]:
        calls.append([question.question for question in questions])
        return {}

    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-once",
        adapter_name="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        profile={},
        answers=[],
        scoped=[],
        resume_json={},
        resume_pdf=tmp_path / "missing.pdf",
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        short_drafter=short_drafter,
    )
    session = AutofillSession(context)

    fields = inspect_fields(browser_page)
    session.fill_with_claude(fields, {"failures": []})
    session.fill_with_claude(fields, {"failures": []})  # a re-rendered step

    assert calls == [["First name", "Why us, in a word?"]]


def submit_form_html() -> str:
    return """
    <form>
      <label for="resume">Resume</label><input id="resume" type="file">
      <label for="first">First name</label><input id="first" name="first_name" required>
      <label for="school">What school are you attending?</label>
      <input id="school" required>
    </form>
    """


def test_ctrl_a_saves_marks_submitted_and_ends(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(submit_form_html())
    browser_page.fill("#school", "Example University")
    resume_pdf = tmp_path / "main.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    api = FakeApi()
    context = SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-ctrl-a",
        adapter_name="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        profile={"legal_name": "Alex Example"},
        answers=[],
        scoped=[],
        resume_json={},
        resume_pdf=resume_pdf,
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        use_claude=False,
        poll_seconds=0.01,
        keys=ScriptedKeys([set(), {"done"}]),
    )

    outcome = AutofillSession(context).run()

    assert outcome["submitted"] is True
    assert api.library == {}
    assert "Marked as submitted." in capsys.readouterr().out


def test_submitting_during_the_claude_pass_ends_the_session_promptly(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content(submit_form_html())
    resume_pdf = tmp_path / "main.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4\n%%EOF")

    def short_drafter(questions: Any, **_: Any) -> dict[str, str]:
        # The user finishes the form and presses Submit while Claude is thinking.
        browser_page.set_content("<h1>Thank you for applying to Acme!</h1>")
        return {"What school are you attending?": "Example University"}

    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-race",
        adapter_name="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        profile={"legal_name": "Alex Example"},
        answers=[],
        scoped=[],
        resume_json={},
        resume_pdf=resume_pdf,
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        short_drafter=short_drafter,
        poll_seconds=0.01,
        keys=InterruptingKeys([set()] * 20),
    )

    outcome = AutofillSession(context).run()

    assert outcome["submitted"] is True
    assert "interrupted" not in outcome


def test_duplicate_labels_keep_control_resolver_after_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_os_worker.autofill import AnswerResolution

    session = state_machine_session()
    session.ctx.answers = []
    session.state.resolutions = {
        "0:home#0": AnswerResolution("Atlanta", "jobs_profile", "profile:city"),
        "0:office#1": AnswerResolution("Rochester", "pattern", "builtin:office"),
    }
    monkeypatch.setattr("personal_os_worker.autofill_session.snapshot_entries", lambda *_: [
        {"question": "Location", "answer_text": value, "control_kind": "text",
         "field_fingerprint": fingerprint}
        for value, fingerprint in [("Atlanta", "home#0"), ("Remote", "office#1")]
    ])
    session.record_snapshot([])
    home, office = session.state.observations
    assert (home["source"], home["resolver_id"]) == ("jobs_profile", "profile:city")
    assert (office["source"], office["resolver_id"], office["proposed_value"]) == (
        "manual", "builtin:office", "Rochester"
    )


def test_no_claude_leaves_unmatched_long_form_manual() -> None:
    session = state_machine_session()
    session.ctx.use_claude = False
    session.ctx.answers = []
    session.ctx.company = "Acme"
    session.ctx.title = "Engineer"
    session.ctx.description = "Role details"
    session.ctx.resume_json = {}
    session.ctx.profile = {}
    session.ctx.scoped = []

    def forbidden_draft(**_: Any) -> str:
        raise AssertionError("no-claude must not invoke the long-form drafter")

    session.ctx.drafter = forbidden_draft
    seed, source, note, _ = session.seed_for("Why this particular team?")
    assert seed == ""
    assert source == "empty"
    assert "disabled" in note


def test_no_claude_still_reuses_an_exact_approved_answer() -> None:
    session = state_machine_session()
    session.ctx.use_claude = False
    session.ctx.answers = [{
        "question": "Why this team?", "normalized_key": "why this team",
        "answer_text": "Approved wording",
        "approved": True, "reuse_policy": "exact",
    }]
    seed, source, _, _ = session.seed_for("Why this team?")
    assert (seed, source) == ("Approved wording", "answer_library")


def test_consent_group_with_two_boxes_announces_each_box_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Greenhouse gives both privacy checkboxes one legend; the echo must not ping-pong."""
    session = state_machine_session()
    session.ctx.answers = []
    entries = [
        {"question": "Applicant Privacy Statement",
         "answer_text": "By clicking this box you consent to our Applicant Privacy Statement.",
         "control_kind": "checkbox", "field_fingerprint": "consent#17"},
        {"question": "Applicant Privacy Statement",
         "answer_text": "By clicking this box you consent to background checks.",
         "control_kind": "checkbox", "field_fingerprint": "consent#18"},
    ]
    monkeypatch.setattr(
        "personal_os_worker.autofill_session.snapshot_entries", lambda *_: entries
    )

    for _ in range(5):
        session.record_snapshot([])

    assert capsys.readouterr().out.count("picked up") == 2


GREENHOUSE_SHORT_ANSWER = (
    '<label for="question_1" class="label">Please share 3-5 sentences explaining your '
    'interest in the Blockchain/Web3 industry.<span aria-hidden="true">*</span></label>'
    '<input id="question_1" type="text" maxlength="255" aria-required="true" value="">'
    '<label for="first_name">First Name</label>'
    '<input id="first_name" type="text" maxlength="255" value="">'
)


def test_greenhouse_short_answer_essay_input_is_seeded_like_a_textarea(
    browser_page,  # type: ignore[no-untyped-def]
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(GREENHOUSE_SHORT_ANSWER)
    session = AutofillSession(cast(Any, SimpleNamespace(
        page=browser_page, browser=FakeBrowser(browser_page), answers=[], use_claude=True,
        company="Gemini", title="Software Engineer", description="Crypto exchange",
        resume_json={}, profile={}, scoped=[],
        drafter=lambda **_: "Short draft that fits.",
    )))

    session.fill_long_form(inspect_fields(browser_page))

    assert browser_page.locator("#question_1").input_value() == "Short draft that fits."
    assert browser_page.locator("#first_name").input_value() == ""
    assert "long-form" in capsys.readouterr().out


def test_a_draft_over_the_controls_cap_is_left_for_the_user(
    browser_page,  # type: ignore[no-untyped-def]
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(GREENHOUSE_SHORT_ANSWER)
    session = AutofillSession(cast(Any, SimpleNamespace(
        page=browser_page, browser=FakeBrowser(browser_page), answers=[], use_claude=True,
        company="Gemini", title="Software Engineer", description="Crypto exchange",
        resume_json={}, profile={}, scoped=[],
        drafter=lambda **_: "x" * 300,
    )))

    session.fill_long_form(inspect_fields(browser_page))

    assert browser_page.locator("#question_1").input_value() == ""
    printed = " ".join(capsys.readouterr().out.split())
    assert "best answer runs 300 characters; the form caps it at 255" in printed
