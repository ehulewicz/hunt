from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.autofill_session import AutofillSession, SessionContext
from personal_os_worker.browser import BrowserClosedError, VisibleBrowser
from personal_os_worker.drafts import DIVIDER, parse_draft

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


class ClosedTab:
    """A `code --wait` process that already exited; the edit is applied on open."""

    def __init__(self, path: Path, edit: str | None) -> None:
        if edit is not None:
            below = path.read_text().split(DIVIDER, 1)[1]
            path.write_text(f"{edit}\n\n{DIVIDER}{below}")

    def poll(self) -> int:
        return 0


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
        draft_dir=tmp_path / "drafts",
    )

    outcome = AutofillSession(context).run()

    assert "browser or application tab was closed" in capsys.readouterr().out.casefold()
    assert outcome == {"final_url": page.url, "answers": []}


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
        draft_dir=tmp_path / "drafts",
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
        draft_dir=tmp_path / "drafts",
    )

    outcome = AutofillSession(context).run()

    assert "browser or application tab was closed" in capsys.readouterr().out.casefold()
    assert outcome == {"final_url": page.url, "answers": []}


def test_session_fills_from_closed_tabs_saves_every_answer_and_ends_on_submit(
    browser_page,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser_page.set_content((FIXTURES / "greenhouse.html").read_text())
    api = FakeApi()
    opened: list[Path] = []
    drafted: list[str] = []

    def opener(path: Path) -> ClosedTab:
        opened.append(path)
        return ClosedTab(path, "Because the edge network is the product.")

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
        draft_dir=tmp_path / "drafts",
        opener=opener,
        drafter=drafter,
        poll_seconds=0.05,
        keys=ScriptedKeys([set(), set(), {"save"}]),
    )
    session = AutofillSession(context)
    polls = {"count": 0}
    original_sleep = session.sleep

    def sleep_then_submit() -> None:
        # The user picks a country, then presses Submit; the confirmation page appears.
        polls["count"] += 1
        if polls["count"] == 2:
            browser_page.select_option("#country", label="Canada")
        if polls["count"] == 4:
            browser_page.set_content("<h1>Thank you for applying to Acme!</h1>")
        original_sleep()

    session.sleep = sleep_then_submit  # type: ignore[method-assign]

    outcome = session.run()

    assert drafted == ["Why this team?"]
    assert len(opened) == 1 and opened[0].name == "03-why-this-team.md"
    draft_text = opened[0].read_text()
    assert "Similar, not exact." in draft_text
    assert parse_draft(draft_text) == "Because the edge network is the product."
    saved = {item["question"]: item for item in outcome["answers"]}
    assert saved["Why this team?"]["answer_text"] == "Because the edge network is the product."
    assert saved["Why this team?"]["source"] == "manual"
    assert saved["Why this team?"]["reuse_policy"] == "contextual"
    assert saved["Country"] == {
        "question": "Country",
        "answer_text": "Canada",
        "source": "manual",
        "reuse_policy": "exact",
    }
    assert saved["First name"]["source"] == "jobs_profile"
    assert saved["Email"]["answer_text"] == "ethan@example.com"
    assert "dry_run" not in outcome
    printed = capsys.readouterr().out
    assert "picked up: Country -> Canada" in printed
    # The scripted Ctrl+S landed after the country was chosen and pushed it to the library.
    assert api.library["Country"]["answer_text"] == "Canada"
    assert api.library["Country"]["approved"] is True
    assert "saved: Country -> Canada" in printed
    assert set(api.library) == {"Country", "Why this team?"}  # profile fills are not re-saved
    assert printed.count("picked up: Country") == 1
    assert "picked up: Why this team?" not in printed  # the tab already reported it
    assert "picked up: First name" not in printed  # the worker filled it


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
        draft_dir=tmp_path / "drafts",
        dry_run=True,
        opener=lambda path: pytest.fail("dry run must not open tabs"),
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
        draft_dir=tmp_path / "drafts",
        opener=lambda path: ClosedTab(path, None),
        drafter=lambda **_: None,
        poll_seconds=0.01,
        keys=InterruptingKeys([set()]),
    )
    (tmp_path / "main.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    outcome = AutofillSession(context).run()
    assert outcome["interrupted"] is True
    saved = {item["question"]: item["answer_text"] for item in outcome["answers"]}
    assert saved["Why this team?"] == "Typed on the page."
    assert saved["First name"] == "Ethan"
