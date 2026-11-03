"""The bank parser, the composer's guards, and the prose tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.jobs.compose import (
    LibraryText,
    Posting,
    compose,
    stated_limit,
)
from personal_os_worker.jobs.library import parse_bank

BANK = Path(__file__).parent / "fixtures" / "pseudo_library.md"
POSTING = Posting(company="Acme", title="SWE Intern", text="Acme builds tools.")


def lib(**kw: Any) -> LibraryText:
    base = dict(code="1.1", section="interest", subject=None, linked=None,
                prompt="Interest?", answer="I built it.")
    return LibraryText(**{**base, **kw})


def test_bank_parses_codes_sections_and_answers() -> None:
    entries = parse_bank(BANK)
    codes = [e["code"] for e in entries]
    assert codes[:8] == ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8"]
    assert "8.1" not in codes and not any(c.startswith("8") for c in codes)
    assert "5.1.E2" in codes and "6.2.P2" in codes and "7.12" in codes
    assert {e["code"]: e["section"] for e in entries}["1.1"] == "interest"
    by_code = {e["code"]: e for e in entries}
    assert by_code["2.2.4"]["subject"] == "Forward Deployed Engineering"
    assert by_code["2.2.4"]["prompt"] == "What draws you to Forward Deployed Engineering"
    assert by_code["5.1.E2"]["subject"].startswith("Northwind")
    assert by_code["5.1.E2"]["prompt"].startswith("The most challenging")
    assert "Anchors" in by_code["5.1.E2"]["guidance"]
    unanswered = {e["code"] for e in entries if not e["answer"]}
    assert unanswered == {"1.3", "1.4", "3.4"}
    # The pseudo bank gives explicit #### 2.2.N headings; the subject, prompt
    # and guidance match what bullet generation produces for the real bank.
    assert by_code["2.2.4"]["subject"] == "Forward Deployed Engineering"
    assert by_code["2.2.4"]["guidance"] == "Two to four sentences, with evidence."
    for e in entries:
        if e["answer"]:
            assert 120 <= len(e["answer"].split()) <= 200


def test_stated_limit() -> None:
    assert stated_limit("Why us? (max 200 words)", None).max_words == 200
    assert stated_limit("150 to 200 words", None).max_words == 200
    assert stated_limit("up to five sentences", None).max_sentences == 5
    assert stated_limit("3 sentences or fewer", None).max_sentences == 3
    assert stated_limit("one or two sentences", None).max_sentences == 2
    assert stated_limit("max 1,000 characters", None).max_chars == 1000
    assert stated_limit("Why?", 500).max_chars == 500
    assert stated_limit("500 characters", 300).max_chars == 300
    assert stated_limit("Why us?", None).text() == ""


def reply(payload: dict[str, Any]) -> list[str]:
    return [json.dumps({"structured_output": payload})]


GOOD = {
    "family": "why_company", "coverage": "full", "sources": ["1.1"],
    "interest": "1.1", "missing_detail": "", "answer": "I built it for Acme.",
}


def test_compose_accepts_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    bad = {**GOOD, "answer": "I; built it."}
    outputs = iter(reply(bad) + reply(GOOD))
    result = compose(question="Why Acme?", max_length=None, posting=POSTING,
                     facts=[], career={}, library=[lib()],
                     runner=lambda argv: (calls.append(argv[-1]), next(outputs))[1])
    assert result.composition is not None and result.attempts == 2
    assert "failed these checks" in calls[1]


def test_compose_retries_a_non_json_reply() -> None:
    calls: list[str] = []
    outputs = iter(["not json at all", *reply(GOOD)])
    result = compose(question="Why Acme?", max_length=None, posting=POSTING,
                     facts=[], career={}, library=[lib()],
                     runner=lambda argv: (calls.append(argv[-1]), next(outputs))[1])
    assert result.composition is not None and result.attempts == 2
    assert "reply is not JSON" in calls[1]


def test_compose_fails_on_non_json_twice() -> None:
    result = compose(question="Q", max_length=None, posting=POSTING,
                     facts=[], career={}, library=[lib()],
                     runner=lambda argv: "not json at all")
    assert result.composition is None and result.attempts == 2
    assert result.failures == ("reply is not JSON",)


def test_compose_fails_twice() -> None:
    bad = {**GOOD, "sources": ["9.9"]}
    result = compose(question="Q", max_length=None, posting=POSTING,
                     facts=[], career={}, library=[lib()],
                     runner=lambda argv: reply(bad)[0])
    assert result.composition is None and result.attempts == 2
    assert any("9.9" in f for f in result.failures)


def test_classify_only_empties_the_answer() -> None:
    result = compose(question="Q", max_length=None, posting=POSTING,
                     facts=[], career={}, library=[lib()], classify_only=True,
                     runner=lambda argv: reply(GOOD)[0])
    assert result.composition is not None and result.composition.answer == ""


def test_missing_needs_no_sources() -> None:
    missing = {**GOOD, "coverage": "missing", "sources": [], "answer": ""}
    result = compose(question="Q", max_length=None, posting=POSTING,
                     facts=[], career={}, library=[lib()],
                     runner=lambda argv: reply(missing)[0])
    assert result.composition is not None
    covered = {**GOOD, "sources": []}
    result = compose(question="Q", max_length=None, posting=POSTING,
                     facts=[], career={}, library=[lib()],
                     runner=lambda argv: reply(covered)[0])
    assert result.composition is None
    assert any("no sources" in f for f in result.failures)


def test_interest_must_be_an_interest_entry() -> None:
    bad = {**GOOD, "interest": "1.2"}
    result = compose(question="Q", max_length=None, posting=POSTING,
                     facts=[], career={}, library=[lib(code="1.2", section="evidence")],
                     runner=lambda argv: reply(bad)[0])
    assert result.composition is None


def test_number_guard_uses_cited_sources() -> None:
    entry = lib(answer="I handled 120+ tickets.")
    good = {**GOOD, "answer": "I handled 120+ tickets at work."}
    ok = compose(question="Q", max_length=None, posting=POSTING,
                 facts=[], career={}, library=[entry],
                 runner=lambda argv: reply(good)[0])
    assert ok.composition is not None
    bad = {**GOOD, "answer": "I handled 999 tickets."}
    no = compose(question="Q", max_length=None, posting=POSTING,
                 facts=[], career={}, library=[entry],
                 runner=lambda argv: reply(bad)[0])
    assert no.composition is None


def test_limit_guard() -> None:
    bad = {**GOOD, "answer": " ".join(["word"] * 50)}
    result = compose(question="max 10 words", max_length=None, posting=POSTING,
                     facts=[], career={}, library=[lib()],
                     runner=lambda argv: reply(bad)[0])
    assert result.composition is None


def test_2_2_children_parse_from_bullets_or_headings(tmp_path: Path) -> None:
    """The real bank's bullet list and explicit #### 2.2.N headings both work."""
    bank = """# Bank

## 2. Direction

### 2.2 Why software

Intro.

- Backend Engineering
- Systems Engineering
"""
    explicit = bank + """Answer: Two sentences of record here.

#### 2.2.1 Backend Engineering

Answer: An explicit answer that runs to one hundred and twenty words is what
this paragraph is building toward one word at a time and it keeps going with
more plain words until the count is safely over the floor so the test can
check that explicit answers survive the parse with their subject and prompt
and guidance intact and a few more words to spare now.
"""
    generated = parse_bank_from(tmp_path / "a.md", bank + "Answer: Two sentences.\n")
    children = [e for e in generated if e["code"].startswith("2.2.")]
    assert [e["code"] for e in children] == ["2.2.1", "2.2.2"]
    assert children[0]["subject"] == "Backend Engineering"
    assert children[0]["answer"] is None
    headed = parse_bank_from(tmp_path / "b.md", explicit)
    child = next(e for e in headed if e["code"] == "2.2.1")
    assert child["subject"] == "Backend Engineering"
    assert child["prompt"] == "What draws you to Backend Engineering"
    assert child["answer"] is not None
    assert not any(e["code"] == "2.2.2" and e["answer"] for e in headed)


def parse_bank_from(path: Path, text: str):
    path.write_text(text, encoding="utf-8")
    return parse_bank(path)


def test_classify_only_skips_answer_guards() -> None:
    bad = {**GOOD, "answer": "Words; with semicolons and 999 invented numbers."}
    result = compose(question="Q", max_length=1, posting=POSTING,
                     facts=[], career={}, library=[lib()], classify_only=True,
                     runner=lambda argv: reply(bad)[0])
    assert result.composition is not None and result.composition.answer == ""


def test_posting_company_and_title_count_as_number_sources() -> None:
    posting = Posting(company="Acme", title="Summer 2027 Intern", text="")
    good = {**GOOD, "answer": "I want to join Acme in Summer 2027."}
    result = compose(question="Q", max_length=None, posting=posting,
                     facts=[], career={}, library=[lib()],
                     runner=lambda argv: reply(good)[0])
    assert result.composition is not None
