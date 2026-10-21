from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.answer_check import Judgement
from personal_os_worker.jobs.audit import Budget
from personal_os_worker.jobs.autofill import normalize_question
from personal_os_worker.jobs.autofill_session import (
    REPAIR_COST,
    AutofillSession,
    SessionContext,
)
from personal_os_worker.jobs.browser import BrowserClosedError, VisibleBrowser, inspect_fields
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.page_state import ApplicationPageState, classify_application_page
from personal_os_worker.jobs.registry.kinds import question_of
from personal_os_worker.jobs.resolve import Knowledge
from personal_os_worker.jobs.short_answers import Verdict

FIXTURES = Path(__file__).parent / "fixtures"


def known(
    facts: dict[str, Any],
    *,
    patterns: list[dict[str, Any]] | None = None,
    prose: list[dict[str, Any]] | None = None,
) -> Knowledge:
    return Knowledge.build(
        facts=[{"key": key, "value": value} for key, value in facts.items()],
        career=None,
        patterns=patterns or [],
        prose=prose or [],
        company="Acme",
        seed="application-1",
    )


def binding(question_key: str, fact_key: str) -> dict[str, Any]:
    return {"id": f"b-{fact_key}", "kind": "binding", "question_key": question_key,
            "fact_key": fact_key, "resolver_id": f"binding:{fact_key}", "status": "active"}


CONTACT = [binding("first name", "identity.first_name"), binding("email", "contact.email")]


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
        self.facts_set: list[tuple[str, Any]] = []
        self.patterns_created: list[dict[str, Any]] = []
        # Accepted answers a learned pattern is replayed against (`/v1/jobs/sightings`).
        self.accepted: list[dict[str, Any]] = []
        self.created_with: list[dict[str, Any]] = []

    def sightings(self) -> list[dict[str, Any]]:
        return self.accepted

    def questions(self) -> list[dict[str, Any]]:
        """Every question a fill event met, as the API records them (R44)."""
        met = {
            str(event["question"])
            for step in self.steps
            for event in step.get("fill_events") or []
        }
        return [{"sample_label": label} for label in sorted(met)]

    def create_pattern(self, **fields: Any) -> dict[str, Any]:
        pattern_id = f"learned-{len(self.patterns_created) + 1}"
        question = fields.get("question")
        created = {
            "id": pattern_id, "resolver_id": f"{fields['kind']}:{pattern_id}",
            "kind": fields["kind"], "expression": fields.get("expression"),
            "question_key": normalize_answer_question(question) if question else None,
            "question_label": question, "fact_key": fields["fact_key"],
            "origin": fields.get("origin"), "status": fields.get("status"),
        }
        self.patterns_created.append(created)
        self.created_with.append(fields)
        return created

    def set_fact(self, key: str, value: Any, **_: Any) -> dict[str, Any]:
        self.facts_set.append((key, value))
        return {"key": key, "value": value}

    def facts(self) -> list[dict[str, Any]]:
        return [{"key": key, "value": value} for key, value in self.facts_set]

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
    session.state.step = 2
    entries = [
        {"question": "Location", "answer_text": "Atlanta", "control_kind": "text",
         "field_fingerprint": "location-primary"},
        {"question": "Location", "answer_text": "Remote", "control_kind": "text",
         "field_fingerprint": "location-secondary"},
    ]
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.snapshot_entries",
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
    snapshots = iter([
        [{"question": "Location", "answer_text": "Atlanta", "control_kind": "text",
          "field_fingerprint": "location#1"}],
        [{"question": "Location", "answer_text": "", "control_kind": "text",
          "field_fingerprint": "location#1"}],
    ])
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.snapshot_entries", lambda *_: next(snapshots)
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
    snapshots = iter([
        [{"question": "Location", "answer_text": "Atlanta", "control_kind": "text",
          "field_fingerprint": "location#1"}],
        [],  # The reader omitted a control it could not read.
    ])
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.snapshot_entries", lambda *_: next(snapshots)
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
    session.state.tier_answers["Location"] = "Atlanta"
    session.state.tier_source["Location"] = "pattern"
    session.state.tier_resolver["Location"] = "builtin:location"
    snapshots = iter([
        [{"question": "Location", "answer_text": value, "control_kind": "text",
          "field_fingerprint": "location#1"}]
        for value in ("Atlanta", "Remote")
    ])
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.snapshot_entries", lambda *_: next(snapshots)
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
    context = SimpleNamespace(page=page, browser=FakeBrowser(page), link=None)
    return AutofillSession(cast(Any, context))


def test_overview_return_stays_in_the_same_wait_step(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session = state_machine_session()
    fields = [{"fingerprint": "email"}]
    states = iter(
        [ApplicationPageState.OVERVIEW, ApplicationPageState.FORM, ApplicationPageState.SUBMITTED]
    )
    monkeypatch.setattr("personal_os_worker.jobs.autofill_session.inspect_fields", lambda _: fields)
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.classify_application_page",
        lambda *_: next(states),
    )
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.has_submit_control", lambda _: False
    )
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
    monkeypatch.setattr("personal_os_worker.jobs.autofill_session.inspect_fields", lambda _: fields)
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.classify_application_page",
        lambda *_: next(states),
    )
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.has_submit_control", lambda _: False
    )
    monkeypatch.setattr("personal_os_worker.jobs.autofill_session.time.monotonic", lambda: 1.0)
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
    monkeypatch.setattr("personal_os_worker.jobs.autofill_session.inspect_fields", lambda _: fields)
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.classify_application_page",
        lambda *_: next(states),
    )
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.has_submit_control", lambda _: False
    )
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.time.monotonic", lambda: next(times)
    )
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
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known({}),
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


def test_active_page_follows_only_tabs_of_this_application(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    """An extension's "what's new" tab took focus mid-run and was read as the form."""
    context = browser_page.context
    body = (
        "<title>tab</title>"
        "<a id='open' href='https://forms.example.org/next' target='_blank'>open</a>"
    )
    context.route("https://**/*", lambda route: route.fulfill(body=body, content_type="text/html"))
    browser = VisibleBrowser("http://unused")
    browser._browser = cast(Any, context.browser)
    application = context.new_page()
    opened: list[Any] = [application]
    try:
        application.goto("https://jobs.example.com/apply")
        advert = context.new_page()
        opened.append(advert)
        advert.goto("https://getadblock.com/whats-new")
        assert browser.active_page(application) == application

        step_two = context.new_page()
        opened.append(step_two)
        step_two.goto("https://jobs.example.com/apply/step-2")
        assert browser.active_page(application) == step_two
        step_two.close()

        with context.expect_page() as popup_info:
            application.click("#open")
        popup = popup_info.value
        opened.append(popup)
        popup.wait_for_load_state()
        assert browser.active_page(application) == popup
    finally:
        for page in opened:
            if not page.is_closed():
                page.close()
        context.unroute("https://**/*")


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
        "personal_os_worker.jobs.autofill_session.reveal_application_form", lambda _: False
    )
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.inspect_fields", closed_inspection
    )
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-race",
        ats="greenhouse",
        browser=cast(Any, browser),
        page=cast(Any, page),
        console_errors=[],
        knowledge=known({}),
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
        "personal_os_worker.jobs.autofill_session.reveal_application_form", lambda _: False
    )
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.inspect_fields", failed_inspection
    )
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-error",
        ats="greenhouse",
        browser=cast(Any, browser),
        page=cast(Any, page),
        console_errors=[],
        knowledge=known({}),
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
        "personal_os_worker.jobs.autofill_session.reveal_application_form", lambda _: False
    )
    browser = VisibleBrowser("http://unused")
    browser._browser = cast(Any, SimpleNamespace(contexts=contexts))
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-closed",
        ats="greenhouse",
        browser=browser,
        page=cast(Any, page),
        console_errors=[],
        knowledge=known({}),
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
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known(
            {"identity.legal_name": "Alex Example", "contact.email": "ethan@example.com"},
            patterns=CONTACT,
        ),
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
    from personal_os_worker.jobs.cover_letter import LetterSlots, load_template

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
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known({"identity.legal_name": "Alex Example"}),
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
    assert saved["Cover Letter"]["form"] == "short"  # the letter is not saved as prose
    printed = capsys.readouterr().out
    assert "Cover letter open in VS Code" in printed
    assert printed.count("drafting the cover letter slots") == 1


def test_cover_letter_pdf_build_failure_reports_the_latex_error_not_a_readback_mismatch(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A renderer that never produces a PDF is an artifact fault, not a DOM one."""
    from personal_os_worker.jobs.cover_letter import LetterSlots, load_template

    browser_page.set_content(
        """
        <form>
          <label for="clf">Cover letter (PDF)</label>
          <input id="clf" name="cover_letter_file" type="file" required>
        </form>
        """
    )
    template_file = tmp_path / "cover_letter.md"
    template_file.write_text(
        "## Letter\n\nDear {{hiring_manager}},\n\n{{hook}} {{proof_primary}}\n\n{{tie}}"
        "\n\n## Modules\n\n### one\n\nProof one.\n"
    )
    message = (
        "latexmk failed for cover_letter: ! LaTeX Error: File `newtxtext.sty' not found. "
        "! ==> Fatal error occurred, no output PDF file produced!"
    )

    def renderer(contact: Any, text: str, directory: Path, *, company: str) -> Path:
        raise RuntimeError(message)

    api = FakeApi()
    context = SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-letter-build",
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known({}),
        resume_json={"contact": {"name": "Alex Example"}},
        resume_pdf=tmp_path / "main.pdf",
        company="Acme",
        title="SWE Intern",
        description="We build things.",
        drafter=lambda **_: None,
        letter_drafter=lambda *_, **__: LetterSlots("", "Hook.", "w", "one", None, "Tie."),
        letter_template=lambda: load_template(template_file),
        editor=lambda path: None,
        letter_renderer=renderer,
        letter_dir=tmp_path / "letters",
        use_claude=True,
        short_drafter=lambda questions, **_: {},
        poll_seconds=0.01,
        keys=ScriptedKeys([set(), {"skip"}]),
    )
    (tmp_path / "main.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    AutofillSession(context).run()

    failures = [failure for step in api.steps for failure in step.get("failures", [])]
    categories = [failure["category"] for failure in failures]
    assert "readback_mismatch" not in categories
    built = [
        failure for failure in failures if failure["category"] == "artifact_build_failed"
    ]
    assert len(built) == 1
    assert "newtxtext.sty" in built[0]["error_message"]
    assert "Rc files read" not in built[0]["error_message"]
    printed = capsys.readouterr().out
    assert "newtxtext.sty" in printed


def test_session_seeds_long_form_records_every_answer_and_ends_on_submit(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content((FIXTURES / "greenhouse.html").read_text())
    api = FakeApi()
    drafted: list[str] = []

    references: list[Any] = []

    def drafter(**kwargs: Any) -> str:
        drafted.append(str(kwargs["question"]))
        references.extend(kwargs["references"])
        assert kwargs["facts"]["contact.phone"] == "5550001111"  # every fact reaches the model
        return "Claude's tailored draft."

    resume = tmp_path / "main.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF")
    context = SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-1",
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known(
            {
                "identity.legal_name": "Alex Example",
                "contact.email": "ethan@example.com",
                "contact.phone": "5550001111",
            },
            patterns=CONTACT,
            prose=[
                {
                    "question_label": "Why this team specifically?",
                    "text": (
                        "Similar, not exact, and long enough to count as writing: the team "
                        "builds the tools I want to build."
                    ),
                }
            ],
        ),
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
    assert saved["Why this team?"]["form"] == "long"
    # Approved writing on a similar question was reference for the draft, not the draft.
    assert [item["question"] for item in references] == ["Why this team specifically?"]
    assert saved["Country"]["answer_text"] == "Canada"
    assert saved["Country"]["source"] == "manual"
    assert saved["Country"]["form"] == "short"
    assert saved["Country"]["control_kind"] == "select"
    # The accepted binding names the fact; the value is the fact's.
    assert saved["First name"]["answer_text"] == "Ethan"
    assert saved["First name"]["source"] == "pattern"
    assert saved["First name"]["resolver_id"] == "binding:identity.first_name"
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


def test_the_model_teaches_a_pattern_and_the_pattern_pass_fills_the_field(
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
          <label for="campus">Which campus will you report to?</label>
          <input id="campus" required>
          <button type="submit">Submit</button>
        </form>
        """
    )
    api = FakeApi()
    asked: list[str] = []

    def short_drafter(questions: Any, **kwargs: Any) -> dict[str, Verdict]:
        asked.extend(str(question.question) for question in questions)
        assert {fact["key"] for fact in kwargs["facts"]} >= {"education.campus"}
        question = "Which campus will you report to?"
        return {question: Verdict(
            question, "fits_fact", fact_key="education.campus",
            expression=r"\bwhich campus\b",
        )}

    resume_pdf = tmp_path / "main.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    context = SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-tiers",
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known(
            {
                "identity.legal_name": "Alex Example",
                "preferences.other_roles": True,
                "employment.organizations": ["Northwind"],
                "education.school_name": "Example Institute of Technology",
                "education.campus": "Atlanta",
            },
            patterns=[
                {"id": "11111111-1111-4111-8111-111111111111", "kind": "expression",
                 "expression": "consider me for other", "fact_key": "preferences.other_roles",
                 "resolver_id": "expression:other-roles", "status": "active"},
                *CONTACT,
                binding("have you ever worked for acme", "employment.worked_for"),
                binding("what school are you attending", "education.school_name"),
            ],
        ),
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

    # Only the question no pattern answered went to the model.
    assert asked == ["Which campus will you report to?"]
    saved = {item["question"]: item for item in outcome["answers"]}
    assert saved["Consider me for other roles?"]["answer_text"] == "Yes"
    assert saved["Consider me for other roles?"]["source"] == "pattern"
    assert saved["Consider me for other roles?"]["resolver_id"] == "expression:other-roles"
    assert saved["Have you ever worked for Acme?"]["answer_text"] == "No - never"
    assert saved["Have you ever worked for Acme?"]["source"] == "pattern"
    assert saved["What school are you attending?"]["answer_text"] == (
        "Example Institute of Technology"
    )
    assert saved["What school are you attending?"]["source"] == "pattern"
    # The model wrote no value: it taught a pattern, checked against every
    # question met, and the pattern pass filled the field from the fact.
    assert [(item["kind"], item["expression"], item["fact_key"], item["origin"],
             item["status"]) for item in api.patterns_created] == [
        ("expression", r"\bwhich campus\b", "education.campus", "model", "active"),
    ]
    assert saved["Which campus will you report to?"]["answer_text"] == "Atlanta"
    assert saved["Which campus will you report to?"]["source"] == "pattern"
    assert saved["Which campus will you report to?"]["resolver_id"] == "expression:learned-1"
    printed = capsys.readouterr().out
    assert "pattern    Consider me for other roles?" in printed and "-> Yes" in printed
    assert "pattern    Have you ever worked for Acme?" in printed and "-> No - never" in printed
    assert "learned    Which campus will you report to?" in printed
    assert "pattern    Which campus will you report to?" in printed
    assert "claude " not in printed
    # Every fill event names the fact it came from; the pattern's id rides along.
    events = [event for step in api.steps for event in step.get("fill_events", [])]
    by_question = {event["question"]: event for event in events if event["operated_ok"]}
    assert by_question["Have you ever worked for Acme?"]["fact_key"] == "employment.worked_for"
    assert by_question["Consider me for other roles?"]["pattern_id"] == (
        "11111111-1111-4111-8111-111111111111"
    )
    campus = by_question["Which campus will you report to?"]
    assert (campus["source"], campus["pattern_id"]) == ("pattern", "learned-1")
    assert all(event["source"] != "model" for event in events)
    assert all(event["knowledge"]["facts"]["education.campus"] == "Atlanta" for event in events)
    assert api.library == {}
    # Nothing accepted was there to contradict it; it was replayed all the same.
    assert api.created_with[0]["verification"] == {"sampled": 0, "failed": 0}
    assert api.created_with[0]["failure"] == {
        "run_id": "run-tiers", "question_key": "which campus will you report to",
    }


def accepted(question: str, answer: str) -> dict[str, Any]:
    return {"question": question, "question_key": question.casefold(), "kind": "text_entry",
            "attributes": {"tag": "input", "type": "text"}, "control": "text",
            "options": None, "answer": answer, "fact_key": None, "value_hash": None,
            "knowledge": {"facts": {"education.campus": "Atlanta",
                                    "work_auth.requires_sponsorship": False},
                          "types": {"work_auth.requires_sponsorship": "boolean"}}}


def test_an_expression_waiting_in_inbox_can_fall_back_to_a_checked_binding(
    browser_page, monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    import httpx

    api = FakeApi()
    create = api.create_pattern

    def refuse_waiting_expression(**fields: Any) -> dict[str, Any]:
        if fields["kind"] == "expression":
            response = httpx.Response(
                409, json={"detail": "this expression already waits in the Inbox"},
                request=httpx.Request("POST", "https://api.test/v1/jobs/patterns"),
            )
            response.raise_for_status()
        return create(**fields)

    monkeypatch.setattr(api, "create_pattern", refuse_waiting_expression)
    session = audit_session(browser_page, facts={"education.campus": "Atlanta"}, api=api)
    question = "Which campus?"
    refusal = session.save_lesson(
        question, "education.campus", r"\bcampus\b", [question],
        {"label": question, "tag": "input", "type": "text"},
    )
    assert refusal is None
    assert [(p["kind"], p["status"]) for p in api.patterns_created] == [("binding", "active")]
    assert api.created_with[0]["verification"] == {"sampled": 0, "failed": 0}


@pytest.mark.parametrize(
    ("sightings", "saved", "filled"),
    [
        # The expression would answer "Do you live on campus?" with a campus: the
        # question alone is bound instead, which changes nothing you accepted.
        ([accepted("Do you live on campus?", "No")], ("binding", "active"), "Atlanta"),
        # You answered this very question otherwise before: nothing is filled, and
        # the binding waits in the Inbox as a pattern check.
        ([accepted("Which campus will you report to?", "Savannah")],
         ("binding", "candidate"), ""),
    ],
)
def test_a_lesson_that_would_change_an_accepted_answer_is_not_kept_as_taught(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sightings: list[dict[str, Any]],
    saved: tuple[str, str],
    filled: str,
) -> None:
    browser_page.set_content(
        """
        <form>
          <label for="campus">Which campus will you report to?</label>
          <input id="campus" required>
          <button type="submit">Submit</button>
        </form>
        """
    )
    api = FakeApi()
    api.accepted = sightings

    def short_drafter(questions: Any, **kwargs: Any) -> dict[str, Verdict]:
        question = "Which campus will you report to?"
        return {question: Verdict(
            question, "fits_fact", fact_key="education.campus", expression=r"\bcampus\b",
        )}

    resume_pdf = tmp_path / "main.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    session = AutofillSession(SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-replay",
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known({"education.campus": "Atlanta"}),
        resume_json={},
        resume_pdf=resume_pdf,
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        short_drafter=short_drafter,
        poll_seconds=0.05,
        keys=InterruptingKeys([set()]),
    ))
    session.run()

    assert [(item["kind"], item["status"]) for item in api.patterns_created] == [saved]
    assert api.created_with[0]["verification"]["sampled"] == 1
    assert browser_page.input_value("#campus") == filled
    printed = capsys.readouterr().out
    if filled:
        assert "expression not kept" in printed and "would get 'Atlanta'" in printed
    else:
        assert "waits in the Inbox as a pattern check" in printed
    assert "claude " not in printed


def test_dry_run_plans_without_opening_tabs_or_writing(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content((FIXTURES / "greenhouse.html").read_text())
    api = FakeApi()
    context = SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-2",
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known({"identity.legal_name": "Alex Example"}),
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
    # The dry run still measures: one event per question, none attempted.
    [step] = api.steps
    events = step["fill_events"]
    questions = {
        normalize_question(question_of(field))
        for field in inspect_fields(browser_page)
        if str(field.get("type") or "") not in {"submit", "button", "reset", "hidden"}
        and field.get("filled") is not True
    }
    assert {normalize_question(event["question"]) for event in events} == questions
    assert all(event["strategy"] is None and event["operated_ok"] is None for event in events)


REVEALING_FORM = """
<form>
  <label for="hisp">Are you Hispanic/Latino?</label>
  <select id="hisp"><option value=""></option><option>Yes</option><option>No</option></select>
  <div id="more"></div>
</form>
<script>
document.querySelector('#hisp').addEventListener('change', () => {
  document.querySelector('#more').innerHTML =
    '<label for="race">Please identify your race</label>' +
    '<select id="race"><option value=""></option><option>Asian</option>' +
    '<option>White</option></select>';
});
</script>
"""


def test_a_question_an_answer_reveals_is_filled_in_its_own_pass(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content(REVEALING_FORM)
    api = FakeApi()
    expression = {"kind": "expression", "status": "active"}
    context = SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-reveal",
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known(
            {"eeo.hispanic_latino": False, "eeo.race_ethnicities": ["White"]},
            patterns=[
                expression | {"id": "e1", "expression": r"\bhispanic\b",
                              "fact_key": "eeo.hispanic_latino", "resolver_id": "expression:e1"},
                expression | {"id": "e2", "expression": r"^(?!.*\bhispanic\b).*\brace\b",
                              "fact_key": "eeo.race_ethnicities", "resolver_id": "expression:e2"},
            ],
        ),
        resume_json={},
        resume_pdf=tmp_path / "missing.pdf",
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
    )
    session = AutofillSession(context)
    fields = inspect_fields(browser_page)
    result = session.fill_deterministic(fields)
    assert browser_page.eval_on_selector("#hisp", "el => el.value") == "No"
    assert browser_page.locator("#race").count() == 1  # revealed, not yet filled
    session.fill_revealed(fields, result)
    assert browser_page.eval_on_selector("#race", "el => el.value") == "White"
    # The revealed pass sent its own events, for the revealed question only.
    revealed = [event["question"] for event in api.steps[-1]["fill_events"]]
    assert revealed == ["Please identify your race"]
    assert any(item.get("label") == "Please identify your race" for item in fields)


def test_a_fact_question_in_a_big_box_gets_the_fact_not_a_draft(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content(
        '<label for="s">Please list the school you will be enrolled in during the internship?'
        '</label>'
        '<textarea id="s"></textarea>'
        '<label for="w">Why do you want to work here?</label><textarea id="w"></textarea>'
    )
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-big-box",
        ats="lever",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known(
            {"education.school_name": "Example Institute of Technology"},
            patterns=[{"id": "e1", "kind": "expression", "status": "active",
                       "expression": r"\bschool\b.{0,40}\byou will be enrolled\b",
                       "fact_key": "education.school_name", "resolver_id": "expression:e1"}],
        ),
        resume_json={},
        resume_pdf=tmp_path / "missing.pdf",
        company="Acme",
        title="Intern",
        description=None,
        use_claude=False,
    )
    session = AutofillSession(context)
    fields = inspect_fields(browser_page)
    session.fill_deterministic(fields)
    session.fill_long_form(fields)
    assert browser_page.input_value("#s") == "Example Institute of Technology"
    assert browser_page.input_value("#w") == ""  # long-form stays yours


def test_explain_in_a_dry_run_shows_every_control_and_operates_none(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content((FIXTURES / "greenhouse.html").read_text())
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-explain",
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known({"identity.legal_name": "Alex Example"}, patterns=CONTACT),
        resume_json={},
        resume_pdf=tmp_path / "missing.pdf",
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        dry_run=True,
        explain=True,
    )
    AutofillSession(context).run()
    out = " ".join(capsys.readouterr().out.split())
    assert "explain · step 1, patterns and engine" in out
    assert "#1 First name" in out and "decided by the binding" in out
    assert "engine: the one resume upload" in out
    assert "Not filled in this pass" in out and "Country" in out
    assert "attempt" not in out  # a dry run operates nothing
    assert browser_page.locator("#first").input_value() == ""


def test_ctrl_c_ends_cleanly_with_the_answers_picked_up_so_far(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content((FIXTURES / "greenhouse.html").read_text())
    browser_page.fill("#why", "Typed on the page.")
    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-3",
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known(
            {"identity.legal_name": "Alex Example", "contact.email": "ethan@example.com"},
            patterns=CONTACT,
        ),
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


def test_claude_is_asked_about_a_question_at_most_twice_per_run(
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
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known({}),
        resume_json={},
        resume_pdf=tmp_path / "missing.pdf",
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        short_drafter=short_drafter,
    )
    session = AutofillSession(context)

    fields = inspect_fields(browser_page)
    for _attempt in range(4):
        session.fill_with_claude(fields, {"failures": []})

    # ASK_LIMIT: the fill pass, and once more after the audit changed something.
    # A third sighting of the same question never asks again.
    assert calls == [
        ["First name", "Why us, in a word?"],
        ["First name", "Why us, in a word?"],
    ]


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
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known({"identity.legal_name": "Alex Example"}),
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

    def short_drafter(questions: Any, **_: Any) -> dict[str, Verdict]:
        # The user finishes the form and presses Submit while Claude is thinking.
        browser_page.set_content("<h1>Thank you for applying to Acme!</h1>")
        question = "What school are you attending?"
        return {question: Verdict(question, "fits_fact", fact_key="education.school_name")}

    context = SessionContext(
        api=FakeApi(),  # type: ignore[arg-type]
        run_id="run-race",
        ats="greenhouse",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known({"identity.legal_name": "Alex Example"}),
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
    from personal_os_worker.jobs.autofill import AnswerResolution

    session = state_machine_session()
    session.state.resolutions = {
        "0:home#0": AnswerResolution("Atlanta", "jobs_profile", "profile:city"),
        "0:office#1": AnswerResolution("Rochester", "pattern", "builtin:office"),
    }
    monkeypatch.setattr("personal_os_worker.jobs.autofill_session.snapshot_entries", lambda *_: [
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
    session.ctx.company = "Acme"
    session.ctx.title = "Engineer"
    session.ctx.description = "Role details"
    session.ctx.resume_json = {}
    session.ctx.knowledge = known({})

    def forbidden_draft(**_: Any) -> str:
        raise AssertionError("no-claude must not invoke the long-form drafter")

    session.ctx.drafter = forbidden_draft
    seed, source, _resolver, note = session.seed_for("Why this particular team?")
    assert seed == ""
    assert source == "empty"
    assert "disabled" in note


def test_no_claude_still_reuses_approved_prose_for_the_same_question() -> None:
    session = state_machine_session()
    session.ctx.use_claude = False
    session.ctx.knowledge = known(
        {},
        prose=[{"id": "p1", "question_label": "Why this team?", "text": "Approved wording"}],
    )
    assert session.seed_for("Why this team?") == (
        "Approved wording", "prose", "prose:p1", "approved prose (edit freely)"
    )
    seed, source, _resolver, _note = session.seed_for("Why that team?")
    assert (seed, source) == ("", "empty")


def test_consent_group_with_two_boxes_announces_each_box_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Greenhouse gives both privacy checkboxes one legend; the echo must not ping-pong."""
    session = state_machine_session()
    entries = [
        {"question": "Applicant Privacy Statement",
         "answer_text": "By clicking this box you consent to our Applicant Privacy Statement.",
         "control_kind": "checkbox", "field_fingerprint": "consent#17"},
        {"question": "Applicant Privacy Statement",
         "answer_text": "By clicking this box you consent to background checks.",
         "control_kind": "checkbox", "field_fingerprint": "consent#18"},
    ]
    monkeypatch.setattr(
        "personal_os_worker.jobs.autofill_session.snapshot_entries", lambda *_: entries
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
        page=browser_page, browser=FakeBrowser(browser_page), use_claude=True,
        company="Gemini", title="Software Engineer", description="Crypto exchange",
        resume_json={}, knowledge=known({}), api=FakeApi(),
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
        page=browser_page, browser=FakeBrowser(browser_page), use_claude=True,
        company="Gemini", title="Software Engineer", description="Crypto exchange",
        resume_json={}, knowledge=known({}), api=FakeApi(),
        drafter=lambda **_: "x" * 300,
    )))

    session.fill_long_form(inspect_fields(browser_page))

    assert browser_page.locator("#question_1").input_value() == ""
    printed = " ".join(capsys.readouterr().out.split())
    assert "best answer runs 300 characters; the form caps it at 255" in printed


# ----- the audit ----------------------------------------------------------


AUDIT_FORM = """
<form>
  <fieldset>
    <legend>Do you require sponsorship?</legend>
    <label><input type="radio" name="sponsor" value="Yes" checked> Yes</label>
    <label><input type="radio" name="sponsor" value="No"> No</label>
  </fieldset>
  <label for="clearance">Do you hold a security clearance?<input id="clearance"
    name="clearance" required></label>
</form>
"""


def audit_session(
    page: Any,
    *,
    facts: dict[str, Any],
    checker: Any = None,
    short_drafter: Any = None,
    ask_facts: bool = False,
    prompt: Any = None,
    api: Any = None,
    patterns: list[dict[str, Any]] | None = None,
    link: Any = None,
) -> AutofillSession:
    context = SessionContext(
        api=cast(Any, api or FakeApi()),
        run_id="run-audit",
        ats="greenhouse",
        browser=cast(Any, FakeBrowser(page)),
        page=page,
        console_errors=[],
        knowledge=known(facts, patterns=patterns),
        resume_json={},
        resume_pdf=Path("missing.pdf"),
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        checker=checker or (lambda answers, **__: {
            answer.question: Judgement(answer.question, "right") for answer in answers
        }),
        short_drafter=short_drafter or (lambda *_, **__: {}),
        ask_facts=ask_facts,
        prompt=prompt or (lambda _: ""),
        link=link,
    )
    return AutofillSession(context)


@pytest.mark.parametrize("past_wrong_answer", [False, True])
def test_the_audit_corrects_an_answer_the_worker_filled_that_a_fact_contradicts(
    browser_page,  # type: ignore[no-untyped-def]
    past_wrong_answer: bool,
) -> None:
    browser_page.set_content(AUDIT_FORM)
    question = "Do you require sponsorship?"

    def checker(answers: Any, **_: Any) -> dict[str, Judgement]:
        assert [item.question for item in answers] == [question]
        assert answers[0].answer == "Yes"
        return {
            question: Judgement(
                question, "wrong", "work_auth.requires_sponsorship", "the fact says no"
            )
        }

    api = FakeApi()
    if past_wrong_answer:
        api.accepted = [accepted(question, "Yes")]
    session = audit_session(
        browser_page, facts={"work_auth.requires_sponsorship": False}, checker=checker, api=api
    )
    # The worker filled it, so correcting it overwrites the worker's own work.
    session.state.tier_source[question] = "pattern"
    fields = inspect_fields(browser_page)

    session.audit_and_repair(fields, {"failures": []})

    checked = {
        str(item.get("label")): item.get("filled")
        for item in inspect_fields(browser_page)
        if item.get("group") == question
    }
    assert checked == {"Yes": False, "No": True}
    # The model named the fact; the question is bound to it and refilled from it.
    assert [(item["kind"], item["question_label"], item["fact_key"], item["origin"])
            for item in api.patterns_created] == [
        ("binding", question, "work_auth.requires_sponsorship", "model"),
    ]
    assert api.patterns_created[0]["status"] == (
        "candidate" if past_wrong_answer else "active"
    )
    events = [event for step in api.steps for event in step.get("fill_events", [])]
    assert (events[-1]["source"], events[-1]["pattern_id"]) == ("pattern", "learned-1")


def test_the_audit_never_rebinds_a_question_you_bound(
    browser_page,  # type: ignore[no-untyped-def]
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(AUDIT_FORM)
    question = "Do you require sponsorship?"
    api = FakeApi()
    yours = binding("do you require sponsorship", "preferences.willing_to_relocate")
    session = audit_session(
        browser_page,
        facts={"work_auth.requires_sponsorship": False, "preferences.willing_to_relocate": True},
        checker=lambda *_, **__: {
            question: Judgement(question, "wrong", "work_auth.requires_sponsorship", "says no")
        },
        api=api,
        patterns=[yours | {"origin": "inbox"}],
    )
    session.state.tier_source[question] = "pattern"

    session.audit_and_repair(inspect_fields(browser_page), {"failures": []})

    assert api.patterns_created == []
    assert browser_page.is_checked("input[value=Yes]")
    assert "a model never overrides yours" in capsys.readouterr().out


def test_the_audit_reports_a_wrong_answer_you_typed_without_overwriting_it(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(AUDIT_FORM)
    question = "Do you require sponsorship?"
    session = audit_session(
        browser_page,
        facts={"work_auth.requires_sponsorship": False},
        checker=lambda *_, **__: {
            question: Judgement(question, "wrong", "work_auth.requires_sponsorship", "says no")
        },
    )
    # No tier filled it, so the snapshot reads it as yours.
    fields = inspect_fields(browser_page)

    session.audit_and_repair(fields, {"failures": []})

    checked = {
        str(item.get("label")): item.get("filled")
        for item in inspect_fields(browser_page)
        if item.get("group") == question
    }
    assert checked == {"Yes": True, "No": False}


def test_the_audit_asks_for_a_missing_fact_and_fills_it_in(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(AUDIT_FORM)
    question = "Do you hold a security clearance?"
    api = FakeApi()
    session = audit_session(
        browser_page,
        facts={},
        ask_facts=True,
        prompt=lambda text: {"fact key": "work_auth.clearance", "label": "Security clearance",
                             "type": "text", "value": "Active Secret"}[
            next(name for name in ("fact key", "label", "type", "value") if name in text)
        ],
        api=api,
    )
    session.state.needed[question] = "security clearance status"
    fields = inspect_fields(browser_page)

    session.audit_and_repair(fields, {"failures": []})

    assert api.facts_set == [("work_auth.clearance", "Active Secret")]
    held = {
        str(item.get("label")): item.get("filled") for item in inspect_fields(browser_page)
    }
    assert held[question] is True


class FakeLink:
    """The agent link: what the session told the page, and facts the page sent."""

    def __init__(self, answers: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[tuple[Any, ...]] = []
        self.answers = list(answers or [])
        self.tabs: list[Any] = []

    def gaps(self, items: list[dict[str, Any]]) -> None:
        self.sent.append(("gaps", items))

    def fact_result(self, question: str, *, ok: bool, message: str) -> None:
        self.sent.append(("fact_result", question, ok, message))

    def fact_answers(self) -> list[dict[str, Any]]:
        answers, self.answers = self.answers, []
        return answers

    def tab(self, page: Any) -> None:
        self.tabs.append(page)


def test_the_audit_sends_its_gaps_to_the_page(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(AUDIT_FORM)
    link = FakeLink()
    session = audit_session(browser_page, facts={}, link=link)
    question = "Do you hold a security clearance?"
    session.state.needed[question] = "security clearance status"

    session.audit_and_repair(inspect_fields(browser_page), {"failures": []})

    kind, items = link.sent[-1]
    assert kind == "gaps"
    item = next(entry for entry in items if entry["question"] == question)
    assert item["cause"] == "fact_gap" and item["settable"] is True
    assert item["missing_fact"] == "security clearance status"


def test_a_fact_the_page_sends_is_set_and_filled_in(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(AUDIT_FORM)
    question = "Do you hold a security clearance?"
    api = FakeApi()
    link = FakeLink([
        {"type": "fact", "question": question, "key": "work_auth.clearance",
         "label": "Security clearance", "value_type": "text", "value": "Active Secret"},
    ])
    session = audit_session(browser_page, facts={}, api=api, link=link)

    session.answer_remote_facts(inspect_fields(browser_page))

    assert api.facts_set == [("work_auth.clearance", "Active Secret")]
    held = {
        str(item.get("label")): item.get("filled") for item in inspect_fields(browser_page)
    }
    assert held[question] is True
    assert link.sent[-1][:3] == ("fact_result", question, True)


def test_a_fact_the_page_cannot_set_is_refused_with_the_reason(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(AUDIT_FORM)
    api = FakeApi()
    link = FakeLink([
        {"type": "fact", "question": "Who referred you?", "key": "referral.referrer_name",
         "value": "Jane Doe"},
        {"type": "fact", "question": "Date of birth", "key": "identity.birth_date",
         "value": "next spring"},
        {"type": "fact", "question": "Odd", "key": "odd.fact", "label": "", "value": "x"},
    ])
    session = audit_session(browser_page, facts={}, api=api, link=link)

    session.answer_remote_facts(inspect_fields(browser_page))

    assert api.facts_set == []
    results = [entry for entry in link.sent if entry[0] == "fact_result"]
    assert [entry[2] for entry in results] == [False, False, False]
    assert "--referred" in results[0][3]
    assert "YYYY-MM-DD" in results[1][3]
    assert "label and a type" in results[2][3]


def test_the_audit_reports_what_no_lane_could_fix(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(AUDIT_FORM)
    session = audit_session(browser_page, facts={})
    fields = inspect_fields(browser_page)

    findings = session.audit(fields, [])

    causes = {item.question: item.cause for item in findings}
    assert causes["Do you hold a security clearance?"] == "pattern_gap"
    assert all(item.cause != "wrong_answer" for item in findings)


def test_the_budget_stops_a_question_from_spending_the_run(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(AUDIT_FORM)
    session = audit_session(browser_page, facts={"contact.email": "a@b.test"})
    session.state.budget = Budget(per_question=REPAIR_COST, per_run=REPAIR_COST)
    fields = inspect_fields(browser_page)
    clearance = "Do you hold a security clearance?"
    field = next(item for item in fields if item.get("label") == clearance)

    first = session.apply_value(
        fields, field, "once", source="model", resolver_id="model:test"
    )
    second = session.apply_value(
        fields, field, "twice", source="model", resolver_id="model:test"
    )

    assert first == "once"
    assert second is None


REFERRAL = "Were you referred by a current employee?"
WHO = "If yes, who referred you?"


def follow_up_form(answer: str, who: str = "") -> str:
    checked = {"Yes": "", "No": ""} | ({answer: "checked"} if answer else {})
    return f"""
    <form>
      <fieldset><legend>{REFERRAL}</legend>
        <label><input type="radio" name="ref" value="Yes" {checked["Yes"]}> Yes</label>
        <label><input type="radio" name="ref" value="No" {checked["No"]}> No</label>
      </fieldset>
      <label for="who">{WHO}</label><input id="who" name="who" value="{who}">
    </form>
    """


REFERRAL_PATTERN = {"id": "e1", "kind": "expression", "expression": r"\breferred by\b",
                    "fact_key": "referral.referred", "resolver_id": "expression:e1",
                    "status": "active"}


def test_the_audit_clears_an_na_the_answer_above_no_longer_rules_out(
    browser_page,  # type: ignore[no-untyped-def]
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(follow_up_form("No"))
    session = audit_session(browser_page, facts={})
    fields = inspect_fields(browser_page)
    session.fill_deterministic(fields)
    assert browser_page.input_value("#who") == "N/A"
    # You answer the referral Yes before the audit looks.
    browser_page.check("input[value=Yes]")

    session.audit_and_repair(inspect_fields(browser_page), {"failures": []})

    assert browser_page.input_value("#who") == ""
    printed = capsys.readouterr().out
    assert "Left for you" in printed and WHO in printed


def test_the_audit_answers_na_once_the_answer_above_rules_a_follow_up_out(
    browser_page,  # type: ignore[no-untyped-def]
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The answer above came from a later tier than the planner's pass."""
    browser_page.set_content(follow_up_form("No"))
    session = audit_session(browser_page, facts={})

    session.audit_and_repair(inspect_fields(browser_page), {"failures": []})

    assert browser_page.input_value("#who") == "N/A"
    assert "every field is filled" in capsys.readouterr().out


def test_the_audit_leaves_an_na_you_typed(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(follow_up_form("Yes", who="N/A"))
    session = audit_session(browser_page, facts={})

    session.audit_and_repair(inspect_fields(browser_page), {"failures": []})

    assert browser_page.input_value("#who") == "N/A"


def test_a_ruled_out_follow_up_in_a_big_box_gets_na_not_a_draft(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        follow_up_form("No").replace(
            f'<label for="who">{WHO}</label><input id="who" name="who" value="">',
            '<label for="why">If yes, please explain.</label><textarea id="why"></textarea>',
        )
    )
    drafted: list[str] = []
    session = audit_session(browser_page, facts={})
    session.ctx.drafter = lambda **kwargs: drafted.append(kwargs["question"]) or "A draft."
    fields = inspect_fields(browser_page)

    session.fill_deterministic(fields)
    session.fill_long_form(fields)

    assert browser_page.input_value("#why") == "N/A"
    assert drafted == []


SPONSORSHIP = "Do you require sponsorship?"
SPONSOR_FORM = f"""
<form>
  <fieldset><legend>{SPONSORSHIP}</legend>
    <label><input type="radio" name="sponsor" value="Yes"> Yes</label>
    <label><input type="radio" name="sponsor" value="No"> No</label>
  </fieldset>
</form>
"""
SPONSOR_PATTERN = {"id": "e2", "kind": "expression", "expression": r"\bsponsorship\b",
                   "fact_key": "work_auth.requires_sponsorship", "resolver_id": "expression:e2",
                   "status": "active"}


def recorded(session: AutofillSession, question: str) -> list[str]:
    """Every answer to this question the run would save: observed or final."""
    return [
        str(item["answer_text"])
        for item in [*session.state.observations, *session.state.collected.values()]
        if item["question"] == question and item["answer_text"]
    ]


def test_a_click_a_fact_contradicts_is_reported_and_not_saved(
    browser_page,  # type: ignore[no-untyped-def]
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(SPONSOR_FORM)
    session = audit_session(
        browser_page, facts={"work_auth.requires_sponsorship": False},
        patterns=[SPONSOR_PATTERN],
    )
    session.fill_deterministic(inspect_fields(browser_page))
    capsys.readouterr()

    browser_page.check("input[value=Yes]")  # the accidental click
    session.record_snapshot(inspect_fields(browser_page))

    assert "Yes" not in recorded(session, SPONSORSHIP)
    printed = capsys.readouterr().out
    assert "picked up" not in printed
    assert "review" in printed and "work_auth.requires_sponsorship" in printed

    browser_page.check("input[value=No]")  # put back
    session.record_snapshot(inspect_fields(browser_page))
    final = [item for item in session.state.collected.values()
             if item["question"] == SPONSORSHIP]
    assert [(item["answer_text"], item["source"]) for item in final] == [("No", "pattern")]


def test_a_click_against_the_fact_a_model_taught_is_not_saved(
    browser_page,  # type: ignore[no-untyped-def]
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(SPONSOR_FORM)
    session = audit_session(
        browser_page, facts={"work_auth.requires_sponsorship": False},
        short_drafter=lambda *_, **__: {
            SPONSORSHIP: Verdict(SPONSORSHIP, "fits_fact",
                                 fact_key="work_auth.requires_sponsorship")
        },
    )
    session.fill_with_claude(inspect_fields(browser_page), {"failures": []})
    assert browser_page.is_checked("input[value=No]")

    browser_page.check("input[value=Yes]")
    session.record_snapshot(inspect_fields(browser_page))

    assert "Yes" not in recorded(session, SPONSORSHIP)
    assert "work_auth.requires_sponsorship" in capsys.readouterr().out


def test_flipping_the_answer_above_says_the_na_below_now_applies(
    browser_page,  # type: ignore[no-untyped-def]
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(follow_up_form(""))
    session = audit_session(
        browser_page, facts={}, patterns=[REFERRAL_PATTERN],
    )
    session.fill_deterministic(inspect_fields(browser_page))
    assert browser_page.input_value("#who") == "N/A"
    session.record_snapshot(inspect_fields(browser_page))
    capsys.readouterr()

    browser_page.check("input[value=Yes]")  # you were referred after all
    session.record_snapshot(inspect_fields(browser_page))

    printed = capsys.readouterr().out
    assert "review" in printed and WHO in printed and "applies" in printed


def test_a_failed_evidence_upload_is_skipped_not_fatal(capsys: pytest.CaptureFixture[str]) -> None:
    import httpx

    class FlakyApi(FakeApi):
        def upload_autofill_artifact(self, run_id: str, *, name: str, **_: Any) -> str:
            if name.endswith(".png"):
                request = httpx.Request("POST", "http://api/artifacts")
                raise httpx.HTTPStatusError(
                    "502", request=request, response=httpx.Response(502, request=request)
                )
            return f"artifacts/{name}"

    context = SimpleNamespace(api=FlakyApi(), run_id="run-flaky", page=None)
    session = AutofillSession(cast(Any, context))
    paths = session.upload_evidence({"page.png": b"x", "page.html": b"y", "more.png": b"z"})
    assert paths == {"page.html": "artifacts/page.html"}
    assert capsys.readouterr().out.count("Could not store failure evidence") == 1


class FakeAccount:
    """An account flow that answers with a set result, optionally changing the page."""

    def __init__(self, outcome: str, reason: str = "", on_run: Any = None) -> None:
        from personal_os_worker.jobs.account import AccountOutcome, AccountResult

        self.email = "e@x.com"
        self.result = AccountResult(AccountOutcome(outcome), reason)
        self.on_run = on_run
        self.calls = 0

    def run(self, page: Any) -> Any:
        self.calls += 1
        if self.on_run is not None:
            self.on_run(page)
        return self.result


SIGN_IN_WALL = (
    Path(__file__).parent / "fixtures" / "account" / "workday_sign_in.html"
).read_text()
SIGNED_IN_FORM = """
<label for=first>First Name</label><input id=first type=text>
<label for=last>Last Name</label><input id=last type=text>
<label for=email>Email</label><input id=email type=email>
<label for=phone>Phone</label><input id=phone type=tel>
"""


def account_session(page: Any, tmp_path: Path, account: Any, **kwargs: Any) -> AutofillSession:
    return AutofillSession(
        SessionContext(
            api=FakeApi(),  # type: ignore[arg-type]
            run_id="run-account",
            ats="workday",
            browser=FakeBrowser(page),  # type: ignore[arg-type]
            page=page,
            console_errors=[],
            knowledge=known({}),
            resume_json={},
            resume_pdf=tmp_path / "resume.pdf",
            company="Acme",
            title="Engineer",
            description=None,
            account=account,
            **kwargs,
        )
    )


def bounded(session: AutofillSession, polls: int) -> None:
    """Let the standby poll a few times, then stop it as Ctrl+C would."""
    count = iter(range(polls))

    def sleep() -> None:
        if next(count, None) is None:
            raise KeyboardInterrupt

    session.sleep = sleep  # type: ignore[method-assign]


def test_account_page_runs_flow_then_fills(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content(SIGN_IN_WALL)
    account = FakeAccount("signed_in", on_run=lambda page: page.set_content(SIGNED_IN_FORM))
    session = account_session(browser_page, tmp_path, account)
    bounded(session, 2)

    fields = session.wait_for_form()

    assert fields is not None
    assert {field.get("label") for field in fields} >= {"First Name", "Email"}
    assert account.calls == 1


def test_account_parked_reports_once(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(SIGN_IN_WALL)
    account = FakeAccount("parked", "captcha on the account page")
    session = account_session(browser_page, tmp_path, account)
    failures: list[tuple[str, str]] = []
    session.report_page_failure = (  # type: ignore[method-assign]
        lambda category, message: failures.append((category, message))
    )
    bounded(session, 3)

    with pytest.raises(KeyboardInterrupt):
        session.wait_for_form()

    assert failures == [("account_wall", "captcha on the account page")]
    assert account.calls == 1
    assert "Account wall:" in capsys.readouterr().out


def test_account_flow_never_runs_on_a_dry_run(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content(SIGN_IN_WALL)
    account = FakeAccount("signed_in")
    session = account_session(browser_page, tmp_path, account, dry_run=True)
    session.report_page_failure = lambda *_: None  # type: ignore[method-assign]
    bounded(session, 2)

    with pytest.raises(KeyboardInterrupt):
        session.wait_for_form()

    assert account.calls == 0


def test_account_page_without_an_email_says_so(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content(SIGN_IN_WALL)
    session = account_session(browser_page, tmp_path, None)
    session.report_page_failure = lambda *_: None  # type: ignore[method-assign]
    bounded(session, 2)

    with pytest.raises(KeyboardInterrupt):
        session.wait_for_form()

    assert capsys.readouterr().out.count("contact.email") == 1


def test_account_flow_never_runs_on_google(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.context.route(
        "https://accounts.google.com/**",
        lambda route: route.fulfill(content_type="text/html", body=SIGN_IN_WALL),
    )
    browser_page.goto("https://accounts.google.com/signin/v2/identifier")
    account = FakeAccount("signed_in")
    session = account_session(browser_page, tmp_path, account)
    session.report_page_failure = lambda *_: None  # type: ignore[method-assign]
    bounded(session, 2)

    with pytest.raises(KeyboardInterrupt):
        session.wait_for_form()

    assert account.calls == 0


def test_a_none_fact_writes_na_and_does_not_ask_for_an_invented_value(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content(
        '<label for="a2">Address Line 2</label><input id="a2" name="a2" required>'
        '<label for="mid">Middle Name</label><input id="mid" name="mid" required>'
    )
    asked: list[list[str]] = []

    def short_drafter(questions: Any, **_: Any) -> dict[str, Verdict]:
        asked.append([question.question for question in questions])
        return {}

    api = FakeApi()
    session = audit_session(
        browser_page,
        facts={"address.line_2": ""},
        patterns=[{"id": "e1", "kind": "expression", "status": "active",
                   "expression": r"^address line 2$", "fact_key": "address.line_2",
                   "resolver_id": "expression:e1"}],
        short_drafter=short_drafter,
        api=api,
    )
    session.ctx.title = "Intern"
    fields = inspect_fields(browser_page)
    result = session.fill_deterministic(fields)
    session.fill_with_claude(fields, result)

    events = [event for step in api.steps for event in step.get("fill_events") or []]
    blank = next(event for event in events if event["question"] == "Address Line 2")
    assert (blank["formatter"], blank["fact_key"], blank["stopped_at"], blank["operated_ok"]) == (
        "none_as_na", "address.line_2", None, True
    )
    assert asked == [["Middle Name"]]
    assert session.state.blanks == {}
    assert browser_page.input_value("#a2") == "N/A"
    findings = {item.question: (item.cause, item.fact_key) for item in session.audit(fields, [])}
    assert findings == {
        "Middle Name": ("pattern_gap", None),
    }


def test_a_question_a_pattern_decides_never_goes_to_the_model(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    """Its fact is missing: that is a fact gap for you, not a question for the model."""
    browser_page.set_content(AUDIT_FORM)
    asked: list[str] = []

    def short_drafter(questions: Any, **_: Any) -> dict[str, Verdict]:
        asked.extend(question.question for question in questions)
        return {}

    session = audit_session(
        browser_page,
        facts={},
        short_drafter=short_drafter,
        patterns=[{"id": "e1", "kind": "expression", "status": "active", "origin": "inbox",
                   "expression": r"\bsecurity clearance\b",
                   "fact_key": "work_auth.security_clearance", "resolver_id": "expression:e1"}],
    )
    fields = inspect_fields(browser_page)
    result = session.fill_deterministic(fields)
    session.fill_with_claude(fields, result)
    session.audit_and_repair(fields, result)

    assert asked == []
    findings = {item.question: item for item in session.audit(fields, [])}
    clearance = findings["Do you hold a security clearance?"]
    assert (clearance.cause, clearance.fact_key) == ("fact_gap", "work_auth.security_clearance")


def test_the_audit_refills_a_cleared_pattern_fill_before_asking_any_model(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        '<label for="city">City</label><input id="city" name="city" required>'
    )
    calls: list[str] = []
    session = audit_session(
        browser_page,
        facts={"address.city": "Atlanta"},
        short_drafter=lambda *_, **__: calls.append("short") or {},
        checker=lambda *_, **__: calls.append("check") or {},
        patterns=[{"id": "e1", "kind": "expression", "status": "active", "origin": "inbox",
                   "expression": r"^city$", "fact_key": "address.city",
                   "resolver_id": "expression:e1"}],
    )
    fields = inspect_fields(browser_page)
    result = session.fill_deterministic(fields)
    assert browser_page.input_value("#city") == "Atlanta"
    # The page re-renders and loses the value.
    browser_page.fill("#city", "")

    session.audit_and_repair(fields, result)

    assert browser_page.input_value("#city") == "Atlanta"
    # Only the answer check ran, after the refill, over the refilled answer.
    assert calls == ["check"]


def test_a_gate_is_answered_through_what_the_model_taught(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    question = "Start Your Application"
    gate = {"label": question, "type": "gateway", "tag": "button",
            "options": ["Autofill with Resume", "Apply Manually"]}
    asked: list[str] = []

    def short_drafter(questions: Any, **_: Any) -> dict[str, Verdict]:
        asked.extend(item.question for item in questions)
        return {question: Verdict(question, "fits_fact", fact_key="preferences.application_start")}

    api = FakeApi()
    session = audit_session(
        browser_page, facts={"preferences.application_start": "Apply Manually"},
        short_drafter=short_drafter, api=api,
    )
    session.ctx.dry_run = True
    # A dry run writes nothing, so it learns nothing and asks nothing.
    assert session.gate_from_claude(gate) is None and asked == []

    session.ctx.dry_run = False
    resolution = session.gate_from_claude(gate)

    assert resolution is not None
    assert (resolution.value, resolution.source, resolution.pattern_id) == (
        "Apply Manually", "pattern", "learned-1"
    )
    assert [(item["kind"], item["question_label"]) for item in api.patterns_created] == [
        ("binding", question)
    ]


def test_a_fact_that_fits_none_of_the_options_is_never_learned(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    """A lesson that could only fail at format would decide the question for good."""
    browser_page.set_content(
        '<label for="t">Which term?</label>'
        '<select id="t" required><option value=""></option><option>Spring</option>'
        '<option>Summer</option></select>'
    )
    api = FakeApi()
    session = audit_session(
        browser_page, facts={"contact.email": "a@b.test"}, api=api,
        short_drafter=lambda *_, **__: {
            "Which term?": Verdict("Which term?", "fits_fact", fact_key="contact.email")
        },
    )
    fields = inspect_fields(browser_page)
    session.fill_with_claude(fields, {"failures": []})

    assert api.patterns_created == []
    assert browser_page.input_value("#t") == ""


def test_the_audit_leaves_a_signature_a_pattern_matches_to_you(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        '<label for="s">Electronic signature: your full legal name</label>'
        '<input id="s" required>'
    )
    session = audit_session(
        browser_page, facts={"identity.legal_name": "Alex Example"},
        patterns=[{"id": "e1", "kind": "expression", "status": "active", "origin": "inbox",
                   "expression": r"\bfull legal name\b", "fact_key": "identity.legal_name",
                   "resolver_id": "expression:e1"}],
    )
    fields = inspect_fields(browser_page)
    session.audit_and_repair(fields, {"failures": []})

    assert browser_page.input_value("#s") == ""
    finding = session.audit(fields, [])[0]
    assert finding.cause != "control_error"


def test_the_audit_never_uploads_a_document_twice(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    """An upload the page does not show by name is still never replayed."""
    browser_page.set_content(
        '<label for="r">Resume</label>'
        '<div style="display:none"><input id="r" type="file" required></div>'
    )
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 synthetic")
    api = FakeApi()
    session = audit_session(browser_page, facts={}, api=api)
    session.ctx.resume_pdf = resume
    fields = inspect_fields(browser_page)
    result = session.fill_deterministic(fields)
    session.audit_and_repair(fields, result)

    uploads = [
        event for step in api.steps for event in step.get("fill_events") or []
        if event["strategy"] == "upload_file"
    ]
    assert len(uploads) == 1


def test_a_gate_a_pattern_decides_is_never_put_to_the_model(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        '<div role="dialog" aria-modal="true"><h2>Start Your Application</h2>'
        '<button class="way">Autofill with Resume</button>'
        '<button class="way">Apply Manually</button></div>'
    )
    asked: list[str] = []
    session = audit_session(
        browser_page, facts={},
        short_drafter=lambda questions, **_: asked.extend(q.question for q in questions) or {},
        patterns=[{"id": "e1", "kind": "expression", "status": "active", "origin": "inbox",
                   "expression": r"^start your application$",
                   "fact_key": "preferences.application_start",
                   "resolver_id": "expression:e1"}],
    )

    assert session.pass_the_gate() is False
    assert asked == []


class WatchingKeys(ScriptedKeys):
    """Notes what the API held at the first wait, then stops the run."""

    def __init__(self, api: FakeApi) -> None:
        super().__init__([])
        self.api = api
        self.held: list[dict[str, Any]] = []

    def wait(self, seconds: float) -> set[str]:
        self.held = [
            observation
            for step in self.api.steps
            for observation in step.get("observations") or []
            if observation["form"] == "long"
        ]
        raise KeyboardInterrupt


def test_writing_is_sent_as_it_is_written_not_at_the_runs_end(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    browser_page.set_content(
        """
        <form>
          <label for="summary">Personal Summary</label>
          <textarea id="summary"></textarea>
          <button type="submit">Submit</button>
        </form>
        """
    )
    api = FakeApi()
    keys = WatchingKeys(api)
    resume_pdf = tmp_path / "main.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    draft = "I build software for business workflows and take it through production."
    session = AutofillSession(SessionContext(
        api=api,  # type: ignore[arg-type]
        run_id="run-writing",
        ats="generic",
        browser=FakeBrowser(browser_page),  # type: ignore[arg-type]
        page=browser_page,
        console_errors=[],
        knowledge=known({}),
        resume_json={},
        resume_pdf=resume_pdf,
        company="Acme",
        title="Backend Engineer Intern",
        description=None,
        drafter=lambda **_: draft,
        poll_seconds=0.05,
        keys=keys,
    ))
    session.run()

    # Already with the API while the run waited on you, with where the text came from.
    assert keys.held and keys.held[0]["answer_text"] == draft
    assert keys.held[0]["provenance"] == [{"start": 0, "end": len(draft), "source": "model"}]
