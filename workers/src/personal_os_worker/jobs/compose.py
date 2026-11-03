"""The composer: one long-form answer written from Ethan's own library entries.

`jobs/compose.py` is a model call site: every call goes through
`model.run(..., purpose="draft")` on argv from `model.argv`. The model names
the family and coverage and reuses his sentences; guards below refuse a reply
that breaks the rules before anything is stored or filled.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

from personal_os_worker.jobs import model
from personal_os_worker.jobs.answers import CLICHES, TAG_LEAK, clean_answer_text

FAMILIES = ("why_company", "why_role", "proudest_work", "technical_deep_dive",
            "hardest_problem", "ownership", "ai_tools", "learning_goals",
            "growth_story", "collaboration", "named_area_experience", "strengths",
            "personal_summary", "anything_else", "other")
COVERAGES = ("full", "composable", "partial", "missing")

NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")
SENTENCES = re.compile(r"[.!?]+(?=\s|$)")
URL = re.compile(r"https?://\S+")
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


@dataclass(frozen=True)
class LibraryText:
    """One answered library entry as the composer sees it."""

    code: str
    section: str
    subject: str | None
    linked: str | None
    prompt: str
    answer: str


@dataclass(frozen=True)
class Posting:
    company: str
    title: str
    text: str


@dataclass(frozen=True)
class Limit:
    max_words: int | None
    max_sentences: int | None
    max_chars: int | None

    def text(self) -> str:
        parts = []
        if self.max_words:
            parts.append(f"at most {self.max_words} words")
        if self.max_sentences:
            parts.append(f"at most {self.max_sentences} sentences")
        if self.max_chars:
            parts.append(f"at most {self.max_chars} characters")
        return "; ".join(parts)


@dataclass(frozen=True)
class Composition:
    family: str
    coverage: str
    sources: tuple[str, ...]
    interest: str
    missing_detail: str
    answer: str


@dataclass(frozen=True)
class Composed:
    composition: Composition | None   # None when no attempt passed the guards
    failures: tuple[str, ...]         # the last attempt's guard failures
    attempts: int                     # model calls made


_WORD_MAXES = re.compile(
    r"(?:at most|no more than|up to|under|less than|fewer than|within|max(?:imum)?(?: of)?)"
    r"\s+(\d[\d,]*)\s*words?|"
    r"(\d[\d,]*)\s*(?:-|to)\s*(\d[\d,]*)\s*words?|"
    r"(\d[\d,]*)\s*words?\s+or\s+(?:less|fewer)|"
    r"(\d[\d,]*)[\s-]words?(?:\s+limit|\s+maximum)?|"
    r"(\d[\d,]*)[\s-]word\b",
    re.IGNORECASE,
)
_SENT_MAXES = re.compile(
    r"(?:at most|no more than|up to|under|less than|fewer than|within|max(?:imum)?(?: of)?)\s+"
    r"(\w+|\d[\d,]*)\s*sentences?|"
    r"(\w+|\d)\s*(?:-|to)\s*(\w+|\d)\s*sentences?|"
    r"(\w+|\d)\s*sentences?\s+or\s+fewer|"
    r"(\w+|\d)\s+or\s+(\w+|\d)\s*sentences?",
    re.IGNORECASE,
)
_CHAR_MAXES = re.compile(
    r"(?:at most|no more than|up to|under|less than|fewer than|within|max(?:imum)?(?: of)?)\s+"
    r"(\d[\d,]*)\s*characters?|"
    r"(\d[\d,]*)\s*characters?\s+or\s+(?:less|fewer)|"
    r"(\d[\d,]*)\s*characters?(?:\s+limit|\s+maximum)?",
    re.IGNORECASE,
)


def _number(text: str) -> int | None:
    text = text.casefold().replace(",", "")
    if text in NUMBER_WORDS:
        return NUMBER_WORDS[text]
    return int(text) if text.isdigit() else None


def _upper(pattern: re.Pattern[str], question: str, *, word: bool = False) -> int | None:
    found: int | None = None
    for match in pattern.finditer(question):
        values = [
            value
            for group in match.groups()
            if group and (value := _number(group)) is not None
        ]
        if values:
            found = max(values) if found is None else max(found, max(values))
    return found


def stated_limit(question: str, max_length: int | None) -> Limit:
    """The length bounds a question states, with maxlength also capping characters."""
    lowered = question.casefold()
    max_chars = _upper(_CHAR_MAXES, lowered)
    if max_length is not None:
        max_chars = min(max_chars, max_length) if max_chars else max_length
    return Limit(
        max_words=_upper(_WORD_MAXES, lowered),
        max_sentences=_upper(_SENT_MAXES, lowered),
        max_chars=max_chars,
    )


# ----- the prompt ---------------------------------------------------------

PROMPT = """You compose one answer to a job application question for Alex Example from his
own handwritten writing.

<question>{question}</question>
<limit>{limit}</limit>
{mode}<posting>{company} | {title}
{posting_text}</posting>
<library>{library}</library>
<career>{career}</career>
<facts>{facts}</facts>

1. Name the question's family, one of: why_company, why_role, proudest_work,
   technical_deep_dive, hardest_problem, ownership, ai_tools, learning_goals,
   growth_story, collaboration, named_area_experience, strengths,
   personal_summary, anything_else, other.
2. Judge coverage from the library alone. The career record and facts supply
   evidence, never voice or motivation.
   full: one entry answers it directly.
   composable: several entries together answer it.
   partial: relevant entries exist but a personal detail the question needs is
   missing.
   missing: no entry is relevant.
   A different wording of something the library answers is not a gap.
3. Unless coverage is missing{classify_clause}, write the answer:
   - Reuse Ethan's sentences from the entries you cite as written wherever they
     fit. Change only what the question or company requires: tense, connecting
     words, the company's name, a detail from the posting. Prefer his wording to
     yours every time.
   - Write new words only to connect passages and to tie them to this posting.
     Never add a claim, number, name, date, feeling or motivation that is not in
     the library, the career record, the facts or the posting.
   - Use an interest entry only when it genuinely relates to what this company
     does according to the posting. If none does, set interest to "none" and
     answer from technical fit and goals. Never invent enthusiasm.
   - Fit the limit. With none stated, write 80 to 150 words.
   - First person, plain sentences. No semicolons, colons, em dashes or en
     dashes. No lists, headings or markdown.
4. Reply with JSON only:
{{"family": "...", "coverage": "full|composable|partial|missing",
 "sources": ["5.1.E2"], "interest": "1.1 or none",
 "missing_detail": "the personal detail that would let this be answered well, or empty",
 "answer": "the answer, or empty when missing or classify-only"}}"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "family": {"type": "string", "enum": list(FAMILIES)},
        "coverage": {"type": "string", "enum": list(COVERAGES)},
        "sources": {"type": "array", "items": {"type": "string"}},
        "interest": {"type": "string"},
        "missing_detail": {"type": "string"},
        "answer": {"type": "string"},
    },
    "required": ["family", "coverage", "sources", "interest", "missing_detail", "answer"],
}

RETRY = ("\n\nYour last reply failed these checks: {failures}. "
         "Reply again with JSON only, following the rules above.")


def _library_block(library: Sequence[LibraryText]) -> str:
    parts = []
    for entry in library:
        where = entry.subject or entry.linked or ""
        parts.append(f"[{entry.code}] {entry.section} | {where} | {entry.prompt}\n{entry.answer}")
    return "\n\n".join(parts)


def _career_block(career: dict[str, Any]) -> str:
    lines: list[str] = []

    def entries_section(title: str, entries: list[dict[str, Any]]) -> None:
        if not entries:
            return
        lines.append(title)
        for entry in entries:
            parts = [str(entry.get(key)) for key in ("title", "org")]
            head = " | ".join(part for part in parts if part)
            meta = ", ".join(str(entry.get(key)) for key in ("location",) if entry.get(key))
            date = str(entry.get("date") or "")
            qualifier = str(entry.get("qualifier") or "")
            tail = " | ".join(part for part in (meta, date, qualifier) if part)
            line = f"- {head}" + (f", {tail}" if tail else "")
            if entry.get("onResume") is False:
                line += " (not on the resume)"
            lines.append(line)
            for bullet in entry.get("bullets") or []:
                lines.append(f"  • {bullet}")
            for bullet in entry.get("moreBullets") or []:
                lines.append(f"  • {bullet} (not on the resume)")

    entries_section("Experience", career.get("experience") or [])
    entries_section("Research", career.get("research") or [])
    entries_section("Projects", career.get("projects") or [])
    entries_section("Activities", career.get("activities") or [])
    schools = career.get("education") or []
    if schools:
        lines.append("Education")
        for school in schools:
            parts = [f"{school.get('school')}, {school.get('location')}".rstrip(", ")]
            for key in ("level",):
                if school.get(key):
                    parts.append(str(school[key]))
            degree = ", ".join(str(school.get(key)) for key in ("degree", "threads")
                               if school.get(key))
            if degree:
                parts.append(degree)
            if school.get("gpa"):
                scale = school.get("gpaScale") or "4.0"
                parts.append(f"GPA {school['gpa']}/{scale}")
            if school.get("graduation"):
                parts.append(str(school["graduation"]))
            courses = school.get("courses") or []
            if courses:
                parts.append("courses " + ", ".join(str(course) for course in courses))
            line = "- " + " | ".join(parts)
            if school.get("onResume") is False:
                line += " (not on the resume)"
            lines.append(line)
    skills = career.get("skills") or []
    if skills:
        lines.append("Skills")
        for skill in skills:
            lines.append(f"- {skill.get('category')}: {skill.get('items')}")
    return "\n".join(lines)


def _facts_block(facts: Sequence[tuple[str, str, Any]]) -> str:
    return "\n".join(
        f"{key} | {label} | {json.dumps(value)}"
        for key, label, value in sorted(facts, key=lambda fact: fact[0])
    )


def _prompt(*, question: str, limit: Limit, posting: Posting,
            library: Sequence[LibraryText], career: dict[str, Any],
            facts: Sequence[tuple[str, str, Any]], classify_only: bool) -> str:
    return PROMPT.format(
        question=question,
        limit=limit.text() or "none stated",
        mode="<mode>classify-only</mode>\n" if classify_only else "",
        company=posting.company, title=posting.title, posting_text=posting.text,
        library=_library_block(library), career=_career_block(career),
        facts=_facts_block(facts),
        classify_clause=", and unless this is classify-only" if classify_only else "",
    )


def _style_failures(raw: str) -> list[str]:
    """The punctuation, cliche, and markup rules an answer breaks, in guard order."""
    failures: list[str] = []
    if ";" in raw:
        failures.append("answer contains a semicolon")
    if "\u2014" in raw or "\u2013" in raw:
        failures.append("answer contains a dash")
    if ":" in URL.sub("", raw):
        failures.append("answer contains a colon")
    lowered = raw.casefold()
    if any(cliche in lowered for cliche in CLICHES):
        failures.append("answer contains a cliche")
    if TAG_LEAK.search(raw):
        failures.append("answer contains markup")
    return failures


def _guard_failures(
    payload: Any, *, library: Sequence[LibraryText], career: dict[str, Any],
    facts: Sequence[tuple[str, str, Any]], posting: Posting, limit: Limit,
    classify_only: bool,
) -> tuple[list[str], dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return ["reply is not a JSON object"], None
    family, coverage = payload.get("family"), payload.get("coverage")
    failures: list[str] = []
    if family not in FAMILIES:
        failures.append("family not one of the listed families")
    if coverage not in COVERAGES:
        failures.append("coverage not one of the listed coverages")
    if not isinstance(payload.get("sources"), list) or not all(
        isinstance(source, str) for source in payload.get("sources") or []
    ):
        failures.append("sources is not a list of strings")
    for key in ("interest", "missing_detail", "answer"):
        if not isinstance(payload.get(key), str):
            failures.append(f"{key} is not a string")
    if failures:
        return failures, None

    codes = {entry.code: entry for entry in library}
    sources = [str(source) for source in payload["sources"]]
    interest = str(payload["interest"])
    for source in sources:
        if source not in codes:
            failures.append(f"source {source} is not an answered library entry")
    if interest != "none" and (
        interest not in codes or codes[interest].section != "interest"
    ):
        failures.append("interest is not 'none' or an answered interest entry")
    if coverage == "missing" and sources:
        failures.append("missing coverage cites sources")
    if coverage != "missing" and not sources:
        failures.append("a covered answer cites no sources")

    answer = str(payload["answer"])
    if classify_only:
        payload = {**payload, "answer": ""}
    elif coverage == "missing" and answer.strip():
        failures.append("a missing coverage still wrote an answer")
    elif coverage != "missing" and not answer.strip():
        failures.append("a covered answer is empty")

    raw = answer
    if classify_only:
        return failures, payload
    failures.extend(_style_failures(raw))

    allowed_numbers = set()
    for code in sources:
        if code in codes:
            allowed_numbers.update(NUMBER.findall(codes[code].answer))
    allowed_numbers.update(NUMBER.findall(_career_block(career)))
    allowed_numbers.update(NUMBER.findall(_facts_block(facts)))
    allowed_numbers.update(NUMBER.findall(posting.text))
    allowed_numbers.update(NUMBER.findall(f"{posting.company} {posting.title}"))
    for number in NUMBER.findall(raw):
        if number not in allowed_numbers:
            failures.append(f"number {number} is in no cited source")
            break

    if limit.max_chars is not None and len(raw) > limit.max_chars:
        failures.append(f"answer is over {limit.max_chars} characters")
    words = len(raw.split())
    if limit.max_words is not None and words > limit.max_words:
        failures.append(f"answer is over {limit.max_words} words")
    if limit.max_sentences is not None:
        sentences = len([piece for piece in SENTENCES.split(raw) if piece.strip()])
        if sentences > limit.max_sentences:
            failures.append(f"answer is over {limit.max_sentences} sentences")
    return failures, payload


DRAFT_RUNNER = partial(model.run, purpose="draft")


def compose(
    *,
    question: str,
    max_length: int | None,
    posting: Posting,
    facts: Sequence[tuple[str, str, Any]],
    career: dict[str, Any],
    library: Sequence[LibraryText],
    classify_only: bool = False,
    attempts: int = 2,
    runner: Callable[[list[str]], str] = DRAFT_RUNNER,
) -> Composed:
    """One composed answer, or None when every attempt failed the guards."""
    limit = stated_limit(question, max_length)
    prompt = _prompt(
        question=question, limit=limit, posting=posting, library=library,
        career=career, facts=facts, classify_only=classify_only,
    )
    failure_list: list[str] = []
    made = 0
    for attempt in range(attempts):
        text = prompt + (RETRY.format(failures="; ".join(failure_list)) if attempt else "")
        made += 1
        payload = model.structured(runner(model.argv(text, schema=SCHEMA)))
        failure_list, parsed = _guard_failures(
            payload, library=library, career=career, facts=facts,
            posting=posting, limit=limit, classify_only=classify_only,
        )
        if not failure_list and parsed is not None:
            return Composed(
                composition=Composition(
                    family=str(parsed["family"]), coverage=str(parsed["coverage"]),
                    sources=tuple(str(s) for s in parsed["sources"]),
                    interest=str(parsed["interest"]),
                    missing_detail=str(parsed["missing_detail"]),
                    answer=clean_answer_text(str(parsed["answer"])) if not classify_only else "",
                ),
                failures=(), attempts=made,
            )
    return Composed(composition=None, failures=tuple(failure_list), attempts=made)
