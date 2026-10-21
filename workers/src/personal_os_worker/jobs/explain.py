"""`apply --explain` and `--step`: every control's whole decision, as it happens.

For each control the trace shows, in the order the system decides:

1. **Control** — the kind it was classified as, the structural attributes that
   identify it across sites, its flags (required, filled, sensitive, consent,
   long-form, in a frame), and its options.
2. **Question** — what the control asks, and the normalized key patterns match.
3. **Planner** — why it was filled or left: an engine rule (consent, upload,
   "how did you hear"), the resolver, the non-answer guard, or a choice group
   whose chosen option is another control.
4. **Resolver** — the binding for this exact question, the expressions that
   match it, which of them decided, the fact and its value, the formatter that
   wrote it for this control, and every other rendering the fact has.
5. **Operation** — the strategies in the order they will be tried, each attempt
   and its error, and the readback: whether the page shows the value.

`--step` pauses before each control is operated, outlines it on the page, and
waits: Enter fills it, `s` leaves it, `c` stops pausing for the rest of the run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from playwright.sync_api import Error, Page
from rich.markup import escape

from personal_os_worker.core.tables import console
from personal_os_worker.jobs.autofill import (
    BLANK_KIND,
    AutofillPlan,
    FormFill,
    choice_group_key,
    is_consent_field,
    is_long_form_field,
    is_sensitive_field,
    offered_options,
)
from personal_os_worker.jobs.browser import fresh_control
from personal_os_worker.jobs.keys import read_key
from personal_os_worker.jobs.registry.kinds import classify, control_attributes, question_of

LABEL_WIDTH = 17
OPTION_PREVIEW = 8
OUTLINE = """(element, on) => {
  const target = element.closest('fieldset, [role=radiogroup], [role=group]') || element;
  target.style.outline = on ? '3px solid #d946ef' : '';
  target.style.outlineOffset = on ? '2px' : '';
}"""


def _line(what: str, detail: str, style: str = "") -> None:
    text = escape(detail)
    styled = f"[{style}]{text}[/{style}]" if style else text
    console.print(f"    [dim]{what:<{LABEL_WIDTH}}[/dim] {styled}")


def control_lines(fields: list[dict[str, Any]], field: dict[str, Any]) -> None:
    """Part 1: what the control is."""
    attributes = control_attributes(field)
    _line("control", f"{classify(field)}  " + "  ".join(
        f"{key}={value}" for key, value in attributes.items()
    ))
    flags = [
        name
        for name, on in (
            ("required", field.get("required") is True),
            ("filled", field.get("filled") is True),
            ("sensitive", is_sensitive_field(field)),
            ("consent", is_consent_field(field)),
            ("long-form", is_long_form_field(field)),
            (f"frame {field.get('frame_index')}", bool(field.get("frame_index"))),
            (f"max {field.get('max_length')} chars", bool(field.get("max_length"))),
        )
        if on
    ]
    _line("flags", ", ".join(flags) or "none")
    options = offered_options(fields, field)
    if options:
        shown = " | ".join(options[:OPTION_PREVIEW])
        more = f"  (+{len(options) - OPTION_PREVIEW} more)" if len(options) > OPTION_PREVIEW else ""
        _line("options", f"{len(options)}: {shown}{more}")
    elif field.get("role") == "combobox":
        _line("options", "none until typed into (a typeahead)")


class Explainer:
    """The `FillObserver` behind `--explain` and `--step`."""

    def __init__(
        self, page: Page, *, step: bool, ask: Callable[[str], str] = read_key
    ) -> None:
        self.page = page
        self.step = step
        self.ask = ask
        self.fields: list[dict[str, Any]] = []
        self.plan = AutofillPlan((), ())
        self.resolver: Any = None
        self.scope: Callable[[dict[str, Any]], bool] = lambda _: True

    def begin(
        self,
        title: str,
        fields: list[dict[str, Any]],
        plan: AutofillPlan,
        resolver: Any,
        *,
        scope: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        """Start a pass; `scope` limits the not-filled listing to the controls it covers."""
        self.fields, self.plan, self.resolver = fields, plan, resolver
        self.scope = scope or (lambda _: True)
        console.print()
        console.rule(f"[bold magenta]explain · {escape(title)}[/bold magenta]", align="left")
        console.print(
            f"  [dim]{len(fields)} controls, {len(plan.fills)} planned; each is shown as "
            "control → question → planner → resolver → operation[/dim]"
        )

    def describe(self, field: dict[str, Any], fill: FormFill | None) -> None:
        """Parts 1 to 4 for one control."""
        index = int(field.get("index") or 0)
        question = question_of(field)
        console.print()
        console.print(f"  [bold]#{index}  {escape(question or '(no label)')}[/bold]")
        control_lines(self.fields, field)
        _line("question", repr(question))
        reason = self.plan.reasons.get(index, "not considered")
        _line("planner", reason)
        explain = getattr(self.resolver, "explain", None)
        through_resolver = reason.startswith("resolver") or "declines to answer" in reason
        if explain is not None and through_resolver:
            for what, detail in explain(
                dict(field) | {"label": question, "options": offered_options(self.fields, field)}
            ):
                _line(what, detail)
        if fill is None:
            return
        if fill.kind == BLANK_KIND:
            _line("fill", "leave it blank: the fact says none")
        elif fill.kind != "text":
            _line("fill", f"upload the {fill.kind}")
        elif fill.chooser is not None:
            _line("fill", f"chosen against the live menu ({fill.chooser})")
        elif fill.clear:
            _line("fill", "clear it")
        else:
            _line("fill", repr(fill.value), "bold")
        if fill.resolution is not None:
            resolution = fill.resolution
            _line("source", " · ".join(
                part for part in (
                    resolution.source, resolution.resolver_id,
                    f"pattern {resolution.pattern_id}" if resolution.pattern_id else "",
                ) if part
            ))

    def _outline(self, field: dict[str, Any], on: bool) -> None:
        try:
            control = fresh_control(self.page, field)
            if on:
                control.scroll_into_view_if_needed(timeout=2000)
            control.evaluate(OUTLINE, on)
        except Error:
            pass

    def before(self, field: dict[str, Any], fill: FormFill, strategies: list[str]) -> bool:
        self.describe(field, fill)
        _line("strategies", " → ".join(strategies) or "none applies")
        if not self.step:
            return True
        self._outline(field, True)
        while True:
            choice = self.ask("    [enter] fill  [s]kip  [c]ontinue without pausing: ")
            choice = choice.strip().casefold()[:1]
            if choice in {"", "f"}:
                return True
            if choice == "s":
                self._outline(field, False)
                _line("operation", "left for you while stepping", "yellow")
                return False
            if choice == "c":
                self.step = False
                return True

    def after(self, field: dict[str, Any], fill: FormFill, outcome: dict[str, Any]) -> None:
        """Part 5: what operating it did."""
        if outcome.get("skipped"):
            _line("operation", f"skipped: {outcome['skipped']}", "yellow")
            return
        for name, error in outcome.get("attempts") or []:
            if error is None:
                _line("attempt", f"{name} ✓ operated", "green")
            else:
                _line("attempt", f"{name} ✗ {error}", "red")
        if outcome.get("operated") is None:
            _line("result", f"no strategy operated: stopped at {outcome.get('stopped_at')}", "red")
        elif outcome.get("verified"):
            _line("readback", f"✓ the page shows {outcome.get('expected')!r}", "green")
        else:
            _line(
                "readback",
                f"✗ expected {outcome.get('expected')!r}, the page shows "
                f"{outcome.get('observed')!r}: stopped at strategy",
                "red",
            )
        if self.step:
            self._outline(field, False)

    def finish(self) -> None:
        """Every control nothing was planned for, once per choice group."""
        planned = {fill.index for fill in self.plan.fills}
        planned_groups = {
            choice_group_key(self.fields[index])
            for index in planned
            if index < len(self.fields) and self.fields[index].get("group")
        }
        seen_groups: set[str] = set()
        left = []
        for field in self.fields:
            index = int(field.get("index") or 0)
            if index in planned or not self.scope(field):
                continue
            group = choice_group_key(field) if field.get("group") else ""
            if group and (group in planned_groups or group in seen_groups):
                continue
            if group:
                seen_groups.add(group)
            left.append(field)
        if not left:
            return
        console.print()
        console.print(f"  [bold]Not filled in this pass[/bold] [dim]({len(left)})[/dim]")
        for field in left:
            self.describe(field, None)

    def dry_run(self) -> None:
        """A dry run: parts 1 to 4 for every control, nothing operated."""
        for fill in self.plan.fills:
            self.describe(self.fields[fill.index], fill)
        self.finish()
