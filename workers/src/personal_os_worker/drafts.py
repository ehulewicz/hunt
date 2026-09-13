"""Long-form answers are drafted in VS Code tabs, not in the terminal.

Each textarea on the live form becomes one Markdown file. The worker seeds the
text above the divider with the best available answer, opens the file with
`code --wait`, and fills the textarea with whatever sits above the divider once
the tab is closed. Everything below the divider is reference material only.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

from personal_os_worker.autofill import normalize_answer_question

DIVIDER = "---- everything below this line is ignored ----"
SIMILAR_THRESHOLD = 0.6
SIMILAR_LIMIT = 5


class DraftProcess(Protocol):
    def poll(self) -> int | None: ...


DraftOpener = Callable[[Path], DraftProcess]


@dataclass(frozen=True, slots=True)
class Draft:
    field_index: int
    question: str
    path: Path
    seed: str
    seed_source: str  # answer_library | model | empty


def draft_slug(question: str, *, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", question.casefold()).strip("-")
    return slug[:limit].rstrip("-") or "question"


def question_similarity(left: str, right: str) -> float:
    return SequenceMatcher(
        None, normalize_answer_question(left), normalize_answer_question(right)
    ).ratio()


def similar_answers(
    question: str,
    answers: list[dict[str, Any]],
    *,
    threshold: float = SIMILAR_THRESHOLD,
    limit: int = SIMILAR_LIMIT,
) -> list[dict[str, Any]]:
    """Rank library answers whose question resembles this one, best first.

    Exact matches are excluded because the caller already handles them as the
    seed; this list is the reference material below the divider.
    """
    key = normalize_answer_question(question)
    scored: list[tuple[float, dict[str, Any]]] = []
    for answer in answers:
        other = str(answer.get("question") or answer.get("normalized_question") or "")
        if not other or not str(answer.get("answer_text") or "").strip():
            continue
        if normalize_answer_question(other) == key:
            continue
        score = question_similarity(question, other)
        if score >= threshold:
            scored.append((score, answer))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [answer for _, answer in scored[:limit]]


def render_draft(
    *,
    seed: str,
    question: str,
    company: str,
    role: str,
    source_note: str,
    similar: list[dict[str, Any]],
) -> str:
    lines = [seed.rstrip(), "", DIVIDER, f"Question: {question}", f"Company: {company} - {role}"]
    lines.append(f"Source: {source_note}")
    lines.append("")
    if similar:
        lines.append("Similar library answers:")
        for answer in similar:
            heading = str(answer.get("question") or answer.get("normalized_question") or "")
            lines.append(f"[{heading}]")
            lines.append(str(answer.get("answer_text") or "").rstrip())
            lines.append("")
    else:
        lines.append("Similar library answers: none")
        lines.append("")
    return "\n".join(lines)


def parse_draft(text: str) -> str:
    """Return the answer above the divider; the whole file if the divider was deleted."""
    head = text.split(DIVIDER, 1)[0]
    return head.strip()


def default_opener(path: Path) -> DraftProcess:
    editor = shutil.which("code") or shutil.which("code.exe")
    if editor is None:
        raise RuntimeError("VS Code command 'code' was not found; install its shell command")
    return subprocess.Popen(
        [editor, "--wait", "--reuse-window", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def write_draft(
    directory: Path,
    *,
    field_index: int,
    question: str,
    seed: str,
    seed_source: str,
    company: str,
    role: str,
    source_note: str,
    similar: list[dict[str, Any]],
) -> Draft:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{field_index:02d}-{draft_slug(question)}.md"
    path.write_text(
        render_draft(
            seed=seed,
            question=question,
            company=company,
            role=role,
            source_note=source_note,
            similar=similar,
        )
    )
    return Draft(
        field_index=field_index,
        question=question,
        path=path,
        seed=seed,
        seed_source=seed_source,
    )
