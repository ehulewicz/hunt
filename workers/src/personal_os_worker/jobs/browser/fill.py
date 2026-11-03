"""The autofill pass and its observer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from playwright.sync_api import Error, Page

from personal_os_worker.jobs import registry
from personal_os_worker.jobs.autofill import (
    BLANK_KIND,
    FILE_KINDS,
    AutofillPlan,
    FormFill,
    Resolves,
    build_autofill_plan,
    offered_options,
)
from personal_os_worker.jobs.registry.events import fill_event
from personal_os_worker.jobs.registry.kinds import classify, control_key, question_of
from personal_os_worker.jobs.registry.strategies import (
    CONTROL_TIMEOUT_MS,
    OPERATIONS,
    Attempt,
    NoMatchingOption,
    candidates,
)
from personal_os_worker.jobs.sites import failure_signature


def plan_autofill(
    page: Page, *, resolver: Resolves | None = None
) -> tuple[list[dict[str, str | bool | None]], AutofillPlan]:
    """Work out what would be filled, without touching the page."""
    from personal_os_worker.jobs.browser.capture import inspect_fields
    from personal_os_worker.jobs.browser.transitions import reveal_application_form
    reveal_application_form(page)
    fields = inspect_fields(page)
    return fields, build_autofill_plan(fields, resolver=resolver)








def stopped_stage(error: BaseException) -> str:
    """Where a failed attempt stopped: the value fit no option, or the widget refused."""
    if isinstance(error, NoMatchingOption) or "unambiguous" in str(error):
        return "format"
    return "strategy"


def failure_category(message: str) -> str:
    return (
        "ambiguous_mapping"
        if "unambiguous" in message
        else "captcha_guard"
        if "captcha" in message.casefold()
        else "stale_locator"
        if "no longer present" in message or "strict mode" in message
        else "fill_exception"
    )


def _attempt_event(
    field: dict[str, Any],
    fill: FormFill,
    strategy: str | None,
    stopped_at: str | None,
    operated_ok: bool | None,
    options: list[str],
) -> dict[str, Any]:
    return fill_event(
        field, strategy=strategy, stopped_at=stopped_at, operated_ok=operated_ok,
        value=fill.value, resolution=fill.resolution, source=fill.chooser, options=options,
    )


class FillObserver(Protocol):
    """Watches each planned fill: `--explain` prints it, `--step` pauses on it."""

    def before(self, field: dict[str, Any], fill: FormFill, strategies: list[str]) -> bool:
        """Called before a control is operated; False leaves it untouched."""
        ...

    def after(self, field: dict[str, Any], fill: FormFill, outcome: dict[str, Any]) -> None:
        """What happened: each attempt and its error, the readback, the value seen."""
        ...


def _learned_attempts(
    page: Page,
    control: Any,
    field: dict[str, Any],
    fill: FormFill,
    attempts: list[tuple[str, str | None]],
    events: list[dict[str, Any]],
    offered: list[str],
    first_error: Exception | None,
    expected: Any,
) -> tuple[str | None, Any, Exception | None, Any]:
    """The site's learned strategies, after every built-in candidate failed."""
    from personal_os_worker.jobs import registry
    from personal_os_worker.jobs.browser.tab import registrable_site
    from personal_os_worker.jobs.registry.api import WidgetHandle

    for strategy in registry.learned("strategies", registrable_site(page.url)):
        if strategy.kind != classify(field):
            continue
        name = f"learned:{strategy.name}"
        try:
            result = strategy.operate(WidgetHandle(page, control), str(fill.value))
        except (Error, RuntimeError) as error:
            first_error = first_error or error
            registry.report(strategy, False)
            attempts.append(
                (name, str(error).splitlines()[0][:200] if str(error) else type(error).__name__)
            )
            events.append(_attempt_event(field, fill, name, stopped_stage(error), False, offered))
            continue
        attempts.append((name, None))
        if result is not None:
            expected = result
        return name, expected, first_error, strategy
    return None, expected, first_error, None


def apply_autofill(
    page: Page,
    fields: list[dict[str, Any]],
    plan: AutofillPlan,
    *,
    resume_path: Path | None,
    assert_active: Callable[[], None],
    transcript_path: Path | None = None,
    preferences: dict[str, str] | None = None,
    observer: FillObserver | None = None,
) -> dict[str, Any]:
    """Operate every planned fill through its control kind's candidate strategies.

    Candidates are tried in order until one operates; every attempt, failed or
    not, becomes a fill event. A failure is recorded and the run moves on. A
    strategy a validated repair found for this exact control goes first.
    """
    from personal_os_worker.jobs.browser.readback import (
        fresh_control,
        verify_control,
    )
    filled_labels: list[str] = []
    skipped: list[str] = []
    applied: dict[int, str] = {}  # values that passed readback, keyed by control
    unanswered = list(plan.unanswered_required_questions)
    failures: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    documents = {"resume": resume_path, "transcript": transcript_path}
    # Uploads first: some forms re-render after one, and none minds the order.
    ordered_fills = sorted(plan.fills, key=lambda fill: 0 if fill.kind in FILE_KINDS else 1)
    for fill in ordered_fills:
        assert_active()
        field = fields[fill.index]
        offered = offered_options(fields, field)
        label = str(field.get("label") or field.get("name") or fill.index)
        document = documents.get(fill.kind)
        if fill.kind == BLANK_KIND:
            # A none fact: the answer is to leave it blank, so nothing is operated.
            events.append(_attempt_event(field, fill, None, None, None, offered))
            if observer is not None:
                observer.after(field, fill, {"skipped": "left blank on purpose"})
            continue
        if fill.kind in FILE_KINDS and document is None:
            skipped.append(f"{label} (no {fill.kind} provided)")
            events.append(_attempt_event(field, fill, None, None, None, offered))
            if observer is not None:
                observer.after(field, fill, {"skipped": f"no {fill.kind} provided"})
            continue
        names = candidates(classify(field), field, fill, has_document=document is not None)
        preferred = (preferences or {}).get(control_key(field))
        if preferred in names:
            names = [preferred, *(name for name in names if name != preferred)]
        if observer is not None and not observer.before(field, fill, names):
            skipped.append(f"{label} (skipped while stepping)")
            continue
        control = fresh_control(page, field)
        expected = fill.value
        operated: str | None = None
        first_error: Exception | None = None
        strategy_tried: Any = None
        attempts: list[tuple[str, str | None]] = []
        for name in names:
            try:
                result = OPERATIONS[name](Attempt(page, control, field, fill, document))
            except (Error, RuntimeError) as error:
                first_error = first_error or error
                attempts.append((name, str(error).splitlines()[0][:200] if str(error) else
                                 type(error).__name__))
                events.append(
                    _attempt_event(field, fill, name, stopped_stage(error), False, offered)
                )
                continue
            attempts.append((name, None))
            operated = name
            if result is not None:
                expected = result
            break
        if operated is None:
            operated, expected, first_error, strategy_tried = _learned_attempts(
                page, control, field, fill, attempts, events, offered,
                first_error, expected,
            )
        if operated is None and first_error is not None:
            message = str(first_error)
            if field.get("required") is True:
                unanswered.append(label)
            failures.append(
                _failure(
                    field, failure_category(message), message, intended=fill.value,
                    stopped_at=stopped_stage(first_error), fill=fill,
                )
            )
            if observer is not None:
                observer.after(field, fill, {
                    "attempts": attempts, "operated": None,
                    "stopped_at": stopped_stage(first_error),
                })
            continue
        is_upload = fill.kind in FILE_KINDS
        observed: str | None = None
        if fill.clear:
            try:
                verified = not fresh_control(page, field).is_checked(timeout=CONTROL_TIMEOUT_MS)
            except Error:
                verified = False
        else:
            verified = verify_control(
                page,
                field,
                expected,
                resume=is_upload,
                filename=document.name if is_upload and document is not None else None,
            )
        if strategy_tried is not None:
            registry.report(strategy_tried, bool(verified))
        events.append(_attempt_event(
            field, fill, operated, None if verified else "strategy", verified, offered
        ))
        if verified:
            filled_labels.append(label)
            if expected is not None and not is_upload:
                applied[fill.index] = (
                    str(field.get("label") or field.get("value") or expected)
                    if field.get("type") in {"radio", "checkbox"} else expected
                )
        else:
            try:
                observed = fresh_control(page, field).input_value(timeout=CONTROL_TIMEOUT_MS)
            except Error:
                observed = None
            failures.append(
                _failure(
                    field,
                    "readback_mismatch",
                    "value did not register",
                    intended=fill.value,
                    observed=observed,
                    stopped_at="strategy",
                    fill=fill,
                )
            )
            if field.get("required") is True:
                unanswered.append(label)
        if observer is not None:
            observer.after(field, fill, {
                "attempts": attempts, "operated": operated, "expected": expected,
                "verified": verified,
                "observed": observed,
                "stopped_at": None if verified else "strategy",
            })
    # The page remains open in the dedicated visible profile for manual review.
    return {
        "captured_url": page.url,
        "filled_fields": filled_labels,
        "skipped_fields": skipped,
        "applied": applied,
        "unanswered_required_questions": list(dict.fromkeys(unanswered)),
        "failures": failures,
        "events": events,
        "submitted": False,
    }
def _failure(
    field: dict[str, Any],
    category: str,
    message: str,
    *,
    intended: str | None = None,
    observed: str | None = None,
    stopped_at: str | None = None,
    fill: FormFill | None = None,
) -> dict[str, Any]:
    fingerprint = str(field.get("fingerprint") or field.get("selector") or field.get("index"))
    resolution = fill.resolution if fill is not None else None
    return {
        # Where it stopped decides whether Repair can retry it (design §10.3).
        "stopped_at": stopped_at,
        "question": question_of(field),
        "fact_key": resolution.fact_key if resolution is not None else None,
        "formatter": resolution.formatter if resolution is not None else None,
        "signature": failure_signature(str(field.get("ats") or "generic"), category, fingerprint),
        "category": category,
        "field_label": field.get("label"),
        "control_kind": field.get("type") or field.get("tag"),
        "field_fingerprint": fingerprint,
        "error_message": message[:2000],
        "intended_value": intended,
        "observed_value": observed,
        "dom_signals": {key: field.get(key) for key in ("tag", "type", "name", "label", "role")},
    }
