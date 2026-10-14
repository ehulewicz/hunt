"""Fill events: one record per attempt to fill a control.

An attempt names the question, the control (kind plus structural attributes),
the strategy tried, the resolver that produced the value, a hash of the value,
and how far it got. The value itself stays in the answer history; the hash is
enough to ask "same value, different outcome?".
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from personal_os_worker.jobs.autofill import (
    AnswerResolution,
    AutofillPlan,
    is_cover_letter_field,
    is_long_form_field,
)
from personal_os_worker.jobs.options import normalize_answer_question, normalize_question
from personal_os_worker.jobs.registry.kinds import classify, control_attributes, question_of

SKIPPED_TYPES = frozenset({"submit", "button", "reset", "hidden"})


def value_hash(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.strip().encode()).hexdigest()


def form_of(field: dict[str, Any]) -> str:
    """Whether a control asks for prose; a cover letter is the letter, not prose."""
    return "long" if is_long_form_field(field) and not is_cover_letter_field(field) else "short"


def fill_event(
    field: dict[str, Any],
    *,
    strategy: str | None,
    stopped_at: str | None,
    operated_ok: bool | None,
    value: str | None = None,
    resolution: AnswerResolution | None = None,
    source: str | None = None,
    fact_key: str | None = None,
    resolver_id: str | None = None,
    formatter: str | None = None,
) -> dict[str, Any]:
    return {
        "question": question_of(field) or str(field.get("fingerprint") or "unlabeled"),
        "kind": classify(field),
        "attributes": control_attributes(field),
        "strategy": strategy,
        "resolver_id": resolution.resolver_id if resolution is not None else resolver_id,
        "fact_key": resolution.fact_key if resolution is not None else fact_key,
        "formatter": resolution.formatter if resolution is not None else formatter,
        "pattern_id": resolution.pattern_id if resolution is not None else None,
        "source": source or (resolution.source if resolution is not None else None),
        "value_hash": value_hash(value),
        "stopped_at": stopped_at,
        "operated_ok": operated_ok,
        "form": form_of(field),
    }


def unresolved_events(
    fields: list[dict[str, Any]],
    plan: AutofillPlan,
    *,
    skip: Callable[[dict[str, Any]], bool] = lambda _: False,
    misses: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """One stop per question the plan left without a value.

    The stop is `format` when a fact was known but fits no option, else
    `pattern`, with whatever fact and resolver the miss named. A choice group is
    one question however many options it has, and it counts as resolved when
    any of its options is planned. A control that already holds a value was not
    attempted and is not an event.
    """
    planned = {fill.index for fill in plan.fills}
    planned_questions = {
        normalize_question(question_of(fields[fill.index])) for fill in plan.fills
    }
    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    for field in fields:
        field_type = str(field.get("type") or "").casefold()
        if (
            int(field["index"]) in planned
            or field.get("filled") is True
            or field_type in SKIPPED_TYPES
            or skip(field)
        ):
            continue
        question = normalize_question(question_of(field))
        if not question or question in planned_questions or question in seen:
            continue
        if field_type in {"radio", "checkbox"} and any(
            other.get("filled") is True
            and normalize_question(question_of(other)) == question
            for other in fields
        ):
            continue
        seen.add(question)
        # Misses are keyed the way the archive keys questions, punctuation-insensitive.
        miss = (misses or {}).get(normalize_answer_question(question_of(field)))
        events.append(
            fill_event(
                field,
                strategy=None,
                stopped_at=getattr(miss, "stage", "pattern"),
                operated_ok=None,
                fact_key=getattr(miss, "fact_key", None),
                resolver_id=getattr(miss, "resolver_id", None),
                formatter=getattr(miss, "formatter", None),
            )
        )
    return events


def planned_events(
    fields: list[dict[str, Any]],
    plan: AutofillPlan,
    *,
    misses: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """What a dry run would attempt: every planned fill, not attempted, plus every gap."""
    return [
        fill_event(
            fields[fill.index],
            strategy=None,
            stopped_at=None,
            operated_ok=None,
            value=fill.value,
            resolution=fill.resolution,
            source=fill.chooser,
        )
        for fill in plan.fills
    ] + unresolved_events(fields, plan, misses=misses)
