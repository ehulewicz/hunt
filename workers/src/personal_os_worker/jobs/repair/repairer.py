"""Retry one failed field on a live page and return the rule that worked (design §10.3).

A `strategy` failure means the value was right but the widget did not take it:
every strategy that applies to the control is tried, each one checked by
readback, not only the ones the live fill reached before giving up. A `format`
failure means the fact fit nothing the control offered: every other rendering of
the fact is tried through the control's strategies. The first attempt that
reads back is the candidate; nothing here makes it live — `os hunt validate`
does, once you have watched it work.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from playwright.sync_api import Error, Page

from personal_os_worker.jobs.autofill import FormFill, choice_group_key
from personal_os_worker.jobs.browser import (
    fresh_control,
    inspect_fields,
    verify_control,
)
from personal_os_worker.jobs.options import choose_option, normalize_answer_question
from personal_os_worker.jobs.registry.formats import renderings
from personal_os_worker.jobs.registry.formats.choices import polarity, yes_no_option
from personal_os_worker.jobs.registry.kinds import classify, control_attributes, question_of
from personal_os_worker.jobs.registry.strategies import APPLIES, CANDIDATES, OPERATIONS, Attempt
from personal_os_worker.jobs.resolve import Knowledge
from personal_os_worker.jobs.safety import refuse_submit

Operation = Callable[[Attempt], str | None]


def find_field(fields: list[dict[str, Any]], question: str) -> dict[str, Any] | None:
    """The first control that asks the question, matched the way questions are keyed."""
    wanted = normalize_answer_question(question)
    return next(
        (field for field in fields if normalize_answer_question(question_of(field)) == wanted),
        None,
    )


def _choice_target(page: Page, field: dict[str, Any], value: str) -> dict[str, Any] | None:
    """The control to operate for this value: the field itself, or, for a radio or
    checkbox, the option of its group that says the value (None when none does).

    A radio's readback is only whether it is checked, so operating the first
    option with any value would "repair" it with nonsense.
    """
    if str(field.get("type") or "").casefold() not in {"radio", "checkbox"}:
        return field
    key = choice_group_key(field)
    group = [
        item for item in inspect_fields(page)
        if str(item.get("type") or "").casefold() in {"radio", "checkbox"}
        and choice_group_key(item) == key
    ] or [field]
    labels = [str(item.get("label") or item.get("value") or "") for item in group]
    chosen = choose_option(labels, value)
    if chosen is None and (answer := polarity(value)) is not None:
        chosen = yes_no_option(labels, answer)
    return group[labels.index(chosen)] if chosen in labels else None


def _attempt(
    page: Page,
    field: dict[str, Any],
    fill: FormFill,
    name: str,
    operations: Mapping[str, Operation],
) -> str | None:
    """Operate with one strategy and read back; the shown value if it took, else None."""
    refuse_submit(field)
    try:
        result = operations[name](Attempt(page, fresh_control(page, field), field, fill, None))
    except (Error, RuntimeError):
        return None
    expected = result if result is not None else fill.value
    try:
        return expected if verify_control(page, field, expected) else None
    except (Error, RuntimeError):
        return None


def repair_strategy(
    page: Page,
    field: dict[str, Any],
    value: str,
    *,
    operations: Mapping[str, Operation] = OPERATIONS,
    order: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    kind = classify(field)
    target = _choice_target(page, field, value)
    if target is None:
        return None
    field = target
    fill = FormFill(index=int(field.get("index") or 0), kind="text", value=value)
    for name in order or CANDIDATES.get(kind, ()):
        applies = APPLIES.get(name)
        if applies is not None and not applies(field, fill, False):
            continue
        shown = _attempt(page, field, fill, name, operations)
        if shown is not None:
            return {
                "kind": "strategy",
                "control_kind": kind,
                "attributes": control_attributes(field),
                "strategy": name,
                "question": question_of(field),
                "value": shown,
            }
    return None


def repair_format(
    page: Page,
    field: dict[str, Any],
    knowledge: Knowledge,
    fact_key: str,
    *,
    failed_formatter: str | None = None,
    operations: Mapping[str, Operation] = OPERATIONS,
) -> dict[str, Any] | None:
    if fact_key not in knowledge.facts:
        return None
    question = question_of(field)
    kind = classify(field)
    for rendering in renderings(
        fact_key,
        knowledge.facts[fact_key],
        knowledge.types.get(fact_key, "text"),
        field=field,
        question=question,
    ):
        if rendering.formatter == failed_formatter:
            continue
        target = _choice_target(page, field, rendering.value)
        if target is None:
            continue
        fill = FormFill(index=int(target.get("index") or 0), kind="text", value=rendering.value)
        for name in CANDIDATES.get(kind, ()):
            applies = APPLIES.get(name)
            if applies is not None and not applies(target, fill, False):
                continue
            shown = _attempt(page, target, fill, name, operations)
            if shown is not None:
                return {
                    "kind": "format",
                    "fact_key": fact_key,
                    "formatter": rendering.formatter,
                    "strategy": name,
                    "question": question,
                    "value": shown,
                }
    return None


def repair_on_page(
    page: Page, issue: dict[str, Any], knowledge: Knowledge
) -> tuple[dict[str, Any] | None, str]:
    """(candidate, what happened) for one issue, on a page already showing the form."""
    occurrence: dict[str, Any] = next(iter(issue.get("occurrences") or []), {})
    signals = dict(occurrence.get("dom_signals") or {})
    question = str(signals.get("question") or issue.get("field_label") or "")
    field = find_field(inspect_fields(page), question)
    if field is None:
        return None, "the question is not on the first page of the form"
    try:
        refuse_submit(field)
    except RuntimeError as error:
        return None, str(error)
    if issue.get("stopped_at") == "format":
        fact_key = str(signals.get("fact_key") or "")
        if not fact_key:
            return None, "the failure did not record which fact it was writing"
        candidate = repair_format(
            page, field, knowledge, fact_key, failed_formatter=signals.get("formatter")
        )
        return candidate, "no other rendering of the fact took" if candidate is None else "ok"
    value = str(occurrence.get("intended_value") or "")
    if not value:
        return None, "the failure did not record the intended value"
    candidate = repair_strategy(page, field, value)
    return candidate, "no strategy took the value" if candidate is None else "ok"
