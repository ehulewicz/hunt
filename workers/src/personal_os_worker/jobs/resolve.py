"""Resolution: which fact answers a field, and how it is rendered (design §8).

The order is fixed: the question's active binding, then active expression
patterns (exactly one fact among those that match). A value always comes from a
fact through a formatter; a pattern only says which fact. Candidates and
rejected patterns are never read — only what you accepted in the Inbox. The
archive is never consulted while applying (§0): it exists for the facts session
and is dropped after it. A question no pattern answers goes to the model pass,
then to you.

A pattern that cannot answer — its fact is missing, or the fact fits none of the
control's options — is kept as a `Miss`, with the stage it stopped at, for the
terminal and the fill events.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from personal_os_worker.jobs.autofill import AnswerResolution
from personal_os_worker.jobs.facts.catalog import CATALOG
from personal_os_worker.jobs.facts.derived import career_facts, derive, posting_facts
from personal_os_worker.jobs.facts.values import fact_values
from personal_os_worker.jobs.formatters import Rendering, render
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.registry.kinds import preference_key

Stage = Literal["pattern", "format"]


def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return "date"
    return "text"


@dataclass(frozen=True, slots=True)
class Knowledge:
    """What one run knows: fact values and types, active patterns, approved prose."""

    facts: dict[str, Any] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    sensitive: frozenset[str] = frozenset()
    patterns: tuple[dict[str, Any], ...] = ()
    prose: tuple[dict[str, Any], ...] = ()
    seed: str = ""
    # Control identity -> the strategy a validated repair found for it.
    preferences: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        facts: list[dict[str, Any]],
        career: dict[str, Any] | None,
        patterns: Iterable[dict[str, Any]] = (),
        prose: Iterable[dict[str, Any]] = (),
        company: str = "",
        seed: str = "",
        today: date | None = None,
        preferences: Iterable[dict[str, Any]] = (),
    ) -> Knowledge:
        """Stored facts over career-derived ones (R51), then derived and posting facts."""
        day = today or date.today()
        stored = fact_values(facts)
        base = {**career_facts(career, day), **stored}
        values = {**derive(base, day), **posting_facts(base, company), **base}
        types: dict[str, str] = {
            key: CATALOG[key].value_type for key in values if key in CATALOG
        }
        types |= {
            str(row["key"]): str(row.get("value_type"))
            for row in facts
            if row.get("key") and row.get("value_type")
        }
        for key, value in values.items():
            types.setdefault(key, _type_of(value))
        labels = {key: CATALOG[key].label for key in values if key in CATALOG}
        labels |= {str(row["key"]): str(row.get("label")) for row in facts if row.get("label")}
        sensitive = {str(row["key"]) for row in facts if row.get("sensitive")}
        sensitive |= {key for key in values if key in CATALOG and CATALOG[key].sensitive}
        return cls(
            facts=values,
            types=types,
            labels=labels,
            sensitive=frozenset(sensitive),
            patterns=tuple(patterns),
            prose=tuple(prose),
            seed=seed,
            preferences={
                preference_key(str(item["control_kind"]), dict(item.get("attributes") or {})):
                str(item["strategy"])
                for item in preferences
                if item.get("control_kind") and item.get("strategy")
            },
        )

    def for_model(self) -> list[dict[str, Any]]:
        """The facts a model call may see: every key and label, no sensitive value (R4)."""
        return [
            {
                "key": key,
                "label": self.labels.get(key, key),
                "type": self.types.get(key),
                "value": "(withheld)" if key in self.sensitive else value,
            }
            for key, value in sorted(self.facts.items())
        ]

    def disclosable(self) -> dict[str, Any]:
        """Fact values that may go into a model prompt."""
        return {key: value for key, value in self.facts.items() if key not in self.sensitive}


@dataclass(frozen=True, slots=True)
class Miss:
    """A question resolution could not answer, and why."""

    question: str
    stage: Stage
    fact_key: str | None = None
    resolver_id: str | None = None
    formatter: str | None = None
    note: str = ""


class Resolver:
    """Answers fields from active patterns, always through a fact."""

    def __init__(self, knowledge: Knowledge) -> None:
        self.knowledge = knowledge
        self.bindings: dict[str, dict[str, Any]] = {}
        self.expressions: list[tuple[re.Pattern[str], dict[str, Any]]] = []
        for pattern in knowledge.patterns:
            if pattern.get("status", "active") != "active":
                continue
            if pattern.get("kind") == "binding" and pattern.get("question_key"):
                self.bindings[normalize_answer_question(str(pattern["question_key"]))] = pattern
            elif pattern.get("kind") == "expression" and pattern.get("expression"):
                try:
                    compiled = re.compile(str(pattern["expression"]), re.IGNORECASE | re.DOTALL)
                except re.error:
                    continue
                self.expressions.append((compiled, pattern))
        self.misses: dict[str, Miss] = {}

    def _miss(self, question: str, miss: Miss) -> None:
        self.misses.setdefault(normalize_answer_question(question), miss)

    def _render(
        self, key: str, field_info: dict[str, Any], question: str, prefer: str | None
    ) -> Rendering | None:
        return render(
            key,
            self.knowledge.facts.get(key),
            self.knowledge.types.get(key, "text"),
            field=field_info,
            question=question,
            prefer=prefer,
            seed=self.knowledge.seed,
        )

    def _from_pattern(
        self, pattern: dict[str, Any], field_info: dict[str, Any], question: str
    ) -> AnswerResolution | None:
        key = str(pattern["fact_key"])
        resolver_id = str(pattern.get("resolver_id") or f"pattern:{pattern.get('id')}")
        if key not in self.knowledge.facts:
            self._miss(
                question,
                Miss(question, "pattern", key, resolver_id, note=f"no fact {key} yet"),
            )
            return None
        rendering = self._render(key, field_info, question, pattern.get("formatter"))
        if rendering is None:
            self._miss(
                question,
                Miss(question, "format", key, resolver_id, pattern.get("formatter"),
                     f"{key} fits none of the options"),
            )
            return None
        return AnswerResolution(
            rendering.value,
            "pattern",
            resolver_id,
            fact_key=key,
            formatter=rendering.formatter,
            pattern_id=str(pattern.get("id")) if pattern.get("id") else None,
        )

    def resolve(self, field_info: dict[str, Any]) -> AnswerResolution | None:
        question = str(field_info.get("label") or "").strip()
        if not question:
            return None
        binding = self.bindings.get(normalize_answer_question(question))
        if binding is not None:
            return self._from_pattern(binding, field_info, question)
        matched = [pattern for regex, pattern in self.expressions if regex.search(question)]
        if len({str(pattern["fact_key"]) for pattern in matched}) == 1:
            return self._from_pattern(matched[0], field_info, question)
        return None


class VerdictResolver:
    """Renders the fact the model named for each question it answered `fits_fact`."""

    def __init__(self, verdicts: dict[str, Any], knowledge: Knowledge) -> None:
        self.knowledge = knowledge
        self.facts = {
            normalize_answer_question(question): str(verdict.fact_key)
            for question, verdict in verdicts.items()
            if getattr(verdict, "fact_key", None)
        }
        self.misses: dict[str, Miss] = {}

    def resolve(self, field_info: dict[str, Any]) -> AnswerResolution | None:
        question = str(field_info.get("label") or "").strip()
        key = self.facts.get(normalize_answer_question(question))
        if key is None or key not in self.knowledge.facts:
            return None
        rendering = render(
            key,
            self.knowledge.facts[key],
            self.knowledge.types.get(key, "text"),
            field=field_info,
            question=question,
            seed=self.knowledge.seed,
        )
        if rendering is None:
            self.misses.setdefault(
                normalize_answer_question(question),
                Miss(question, "format", key, "model:fact_v1",
                     note=f"the model chose {key}, which fits none of the options"),
            )
            return None
        return AnswerResolution(
            rendering.value, "model", "model:fact_v1", fact_key=key, formatter=rendering.formatter
        )
