"""Long-form fill: composed from the narrative library first, the drafter as fallback."""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Error, sync_playwright
from test_autofill_session import audit_session

from personal_os_worker.jobs.browser import inspect_fields
from personal_os_worker.jobs.compose import (
    Composed,
    Composition,
    LibraryText,
    Posting,
)
from personal_os_worker.jobs.resolve import Knowledge

LIB = (
    LibraryText(code="1.2", section="story", subject=None, linked=None,
                prompt="A story?", answer="The story."),
    LibraryText(code="4.7", section="evidence", subject=None, linked=None,
                prompt="Evidence?", answer="The evidence."),
)


def composed(answer: str, *, coverage: str = "full") -> Composed:
    return Composed(
        composition=Composition(
            family="why_company", coverage=coverage,
            sources=("1.2", "4.7"), interest="none", missing_detail="",
            answer=answer,
        ),
        failures=(), attempts=1,
    )


def session_with(
    composer: Any, drafter: Any, *, use_claude: bool = True,
    library: Any = LIB, knowledge: Knowledge | None = None,
) -> Any:
    session = audit_session(None, facts={})
    if knowledge is not None:
        session.ctx.knowledge = knowledge
    session.ctx.library = library
    session.ctx.composer = composer
    session.ctx.drafter = drafter
    session.ctx.use_claude = use_claude
    return session


def test_a_composed_answer_is_the_seed() -> None:
    calls: list[dict[str, Any]] = []
    drafter_calls: list[dict[str, Any]] = []
    session = session_with(
        lambda **kwargs: (calls.append(kwargs), composed("My answer."))[1],
        lambda **kwargs: drafter_calls.append(kwargs) or "A draft.",
    )

    assert session.seed_for("Why Acme?", limit=500) == (
        "My answer.", "prose", "library:1.2,4.7",
        "composed from library 1.2, 4.7",
    )
    assert drafter_calls == []
    kwargs = calls[0]
    assert kwargs["max_length"] == 500 and isinstance(kwargs["max_length"], int)
    assert kwargs["posting"] == Posting("Acme", "Backend Engineer Intern", "")
    assert kwargs["career"] == session.ctx.resume_json
    assert kwargs["library"] == LIB
    assert kwargs["attempts"] == 2


def test_missing_coverage_falls_back_to_the_drafter() -> None:
    session = session_with(
        lambda **_: composed("", coverage="missing"),
        lambda **_: "A claude draft.",
    )
    assert session.seed_for("Why Acme?") == (
        "A claude draft.", "model", "model:long_form_v1",
        "claude draft tailored to this posting",
    )


def test_failed_guards_fall_back_to_the_drafter() -> None:
    session = session_with(
        lambda **_: Composed(composition=None, failures=("a cliche",), attempts=2),
        lambda **_: "A claude draft.",
    )
    assert session.seed_for("Why Acme?") == (
        "A claude draft.", "model", "model:long_form_v1",
        "claude draft tailored to this posting",
    )


def test_no_claude_composes_nor_drafts() -> None:
    calls: list[str] = []
    session = session_with(
        lambda **_: calls.append("composer") or composed("x"),
        lambda **_: calls.append("drafter") or "x",
        use_claude=False,
    )
    seed, source, _resolver, _note = session.seed_for("Why Acme?")
    assert (seed, source) == ("", "empty")
    assert calls == []


def test_a_question_is_seeded_at_most_once_a_run() -> None:
    calls: list[str] = []
    session = session_with(
        lambda **_: calls.append("composer") or composed("My answer."),
        lambda **_: calls.append("drafter") or "x",
    )
    assert session.seed_for("Why Acme?")[0] == "My answer."
    seed, source, _resolver, note = session.seed_for("Why Acme?")
    assert (seed, source) == ("", "empty")
    assert "already tried" in note
    assert calls == ["composer"]


def test_an_empty_library_goes_straight_to_the_drafter() -> None:
    calls: list[str] = []
    session = session_with(
        lambda **_: calls.append("composer") or composed("x"),
        lambda **_: calls.append("drafter") or "A claude draft.",
        library=(),
    )
    assert session.seed_for("Why Acme?")[:2] == ("A claude draft.", "model")
    assert calls == ["drafter"]


def test_references_rank_stored_prose_exact_match_first() -> None:
    session = session_with(lambda **_: None, lambda **_: None)
    session.ctx.knowledge = Knowledge.build(
        facts=[], career=None,
        prose=[
            {"question_label": "Why this team?", "text": "exact"},
            {"question_label": "Why this team specifically?", "text": "a"},
            {"question_label": "Why this role?", "text": "b"},
            {"question_label": "Why this company?", "text": "c"},
            {"question_label": "Why this employer?", "text": "d"},
            {"question_label": "Why this organization?", "text": "e"},
            {"question_label": "What is your GitHub URL?", "text": "github"},
            {"question_label": "Empty answer", "text": ""},
        ],
        company="Acme", seed="application-1",
    )
    refs = session.references("Why this team?")
    assert refs[0]["answer_text"] == "exact"
    assert len(refs) <= 5
    assert "github" not in [item["answer_text"] for item in refs]
    assert all(item["answer_text"] for item in refs)
    # The result is cached for the run, not recomputed per ask.
    assert session.references("Why this team?") is refs


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


def test_fill_long_form_writes_composed_and_flags_only_drafts(
    browser_page,  # type: ignore[no-untyped-def]
) -> None:
    browser_page.set_content(
        '<form><label for="s">Why Acme?</label><textarea id="s"></textarea>'
        '<label for="t">Why this role?</label><textarea id="t"></textarea></form>'
    )
    session = audit_session(browser_page, facts={})
    session.ctx.library = LIB

    def composer(**kwargs: Any) -> Composed:
        if kwargs["question"] == "Why Acme?":
            return composed("Composed answer.")
        return Composed(composition=None, failures=("a guard",), attempts=2)

    session.ctx.composer = composer
    session.ctx.drafter = lambda **_: "A claude draft."

    session.fill_long_form(inspect_fields(browser_page))

    assert browser_page.input_value("#s") == "Composed answer."
    assert browser_page.input_value("#t") == "A claude draft."
    sources = {item.source for item in session.state.resolutions.values()}
    assert sources == {"prose", "model"}
    resolvers = {item.resolver_id for item in session.state.resolutions.values()}
    assert "library:1.2,4.7" in resolvers
    # Only the fallback draft is flagged for review.
    assert session.state.review == [("Why this role?", "claude draft")]
