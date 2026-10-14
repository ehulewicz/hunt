"""Consult the archive for a question no active pattern answers (design §14).

The archive answers which fact a question asks about, never what value to type
(R41). A hit names the fact key, the retired rule that recognised the question,
and the formatter that rule implied; any archived answers to the very same
question come along as hints, for you to read, and are never filled.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from personal_os_worker.jobs.archive.rules import RULES, TIERS, ArchivedRule


@dataclass(frozen=True, slots=True)
class ArchiveHint:
    """An archived answer to the same question: shown, never typed."""

    answer_id: str
    answer: str


@dataclass(frozen=True, slots=True)
class ArchiveHit:
    fact_key: str | None
    rule: ArchivedRule | None
    hints: tuple[ArchiveHint, ...] = field(default=())

    @property
    def resolver_id(self) -> str | None:
        """How a fill from this hit is attributed: `archive:<tier>:<rule>`."""
        return f"archive:{self.rule.ref}" if self.rule is not None else None

    @property
    def formatter(self) -> str | None:
        return self.rule.formatter if self.rule is not None else None


# Required-field markers some boards leave at the end of a label ("Email\u2731").
REQUIRED_MARK = re.compile(r"[\s*\u2731]+$")


def normalize_label(value: str) -> str:
    """The exact-label map's normalization: collapsed whitespace, casefolded, marks dropped."""
    return REQUIRED_MARK.sub("", re.sub(r"\s+", " ", value).strip()).casefold()


def answer_key(value: str) -> str:
    """The old library's key: punctuation-insensitive, as `jobs_archive_answers` stores it."""
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", value.casefold()).split())


def translate(question: str, field_info: dict[str, Any] | None = None) -> ArchivedRule | None:
    """The one retired rule that says which fact this question asks about, if any.

    The first tier with a match decides; inside it, every matching rule must
    name the same fact, or the question passes through untranslated.
    """
    info = field_info or {}
    label = normalize_label(question)
    for tier in TIERS:
        matched: list[ArchivedRule] = []
        for rule in (item for item in RULES if item.tier == tier):
            subject = label if tier == "label" else question
            if rule.pattern.search(subject) is None:
                continue
            if rule.when is not None and not rule.when(info):
                continue
            matched.append(rule)
        if not matched:
            continue
        facts = {rule.fact_key for rule in matched}
        return matched[0] if len(facts) == 1 else None
    return None


def lookup(
    question: str,
    *,
    archive_answers: Iterable[dict[str, Any]] = (),
    field_info: dict[str, Any] | None = None,
) -> ArchiveHit | None:
    """What the archive knows about a question: its fact, and old answers as hints."""
    if not question.strip():
        return None
    key = answer_key(question)
    hints = tuple(
        ArchiveHint(str(row.get("id") or ""), str(row.get("answer_text") or "").strip())
        for row in archive_answers
        if str(row.get("reuse_policy") or "exact") != "pattern"
        and answer_key(str(row.get("normalized_key") or row.get("question") or "")) == key
        and str(row.get("answer_text") or "").strip()
    )
    rule = translate(question, field_info)
    if rule is None and not hints:
        return None
    return ArchiveHit(rule.fact_key if rule else None, rule, hints)
