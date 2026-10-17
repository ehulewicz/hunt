"""The interactive part of `os hunt apply`, without any terminal prompts.

The terminal only reports. A field fills from a fact: through the pattern you
accepted for its question, else through the model naming the fact (design §8).
The archive is never consulted while applying. Every textarea is seeded with
approved prose or a Claude draft, and drafts are listed for review. Everything
still empty — required or optional — is outlined on the page and listed with why
it is empty; the user types it, the worker polls the page, saves every answer it
can see, and watches for a visible Submit control. The user owns Next and
Submit; the run ends when the page reports submission or when the user
interrupts, and both paths save the collected answers.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import Error, Page
from rich.markup import escape

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.tables import console
from personal_os_worker.jobs.answer_check import (
    WORKER_SOURCES,
    FilledAnswer,
    Judgement,
    check_answers,
)
from personal_os_worker.jobs.answers import draft_contextual_answer
from personal_os_worker.jobs.audit import (
    REPAIRABLE_CAUSES,
    Budget,
    Finding,
    classify_empty,
    order,
    summarize,
)
from personal_os_worker.jobs.autofill import (
    AnswerResolution,
    is_cover_letter_field,
    is_long_form_field,
    is_resume_field,
    is_sensitive_field,
)
from personal_os_worker.jobs.autofill_timing import AutofillTimings
from personal_os_worker.jobs.browser import (
    BrowserClosedError,
    VisibleBrowser,
    apply_autofill,
    audit_filled_form,
    combobox_options,
    fresh_control,
    has_submit_control,
    highlight_unfilled,
    inspect_fields,
    is_browser_closed_error,
    page_evidence,
    plan_autofill,
    reveal_application_form,
    set_registered_value,
    snapshot_entries,
    verify_control,
)
from personal_os_worker.jobs.cover_letter import (
    LetterSlots,
    LetterTemplate,
    draft_slots,
    edit_in_vscode,
    letter_pdf,
    load_template,
    placeholder_slots,
    render_letter,
)
from personal_os_worker.jobs.drafts import similar_answers
from personal_os_worker.jobs.explain import Explainer, group_options
from personal_os_worker.jobs.formatters import render
from personal_os_worker.jobs.gateway import (
    click_gateway,
    form_appeared,
    inspect_gateways,
    observed_choice,
)
from personal_os_worker.jobs.keys import KeyWatcher, NoKeys
from personal_os_worker.jobs.options import (
    choose_option,
    normalize_answer_question,
    normalize_question,
)
from personal_os_worker.jobs.page_state import (
    ApplicationPageState,
    TabNavigation,
    capture_tab_navigation,
    classify_application_page,
)
from personal_os_worker.jobs.registry.events import (
    fill_event,
    planned_events,
    unresolved_events,
)
from personal_os_worker.jobs.registry.kinds import question_of
from personal_os_worker.jobs.repair.repairer import find_field, repair_format, repair_strategy
from personal_os_worker.jobs.resolve import Knowledge, Miss, Resolver, VerdictResolver
from personal_os_worker.jobs.short_answers import ShortQuestion, Verdict, draft_short_answers
from personal_os_worker.jobs.sites import failure_signature


class SubmittedByUser(Exception):
    """Ctrl+A: the user says the application went in."""


class SkippedByUser(Exception):
    """Ctrl+D: the user passes on this listing; it leaves the board without an application."""


Drafter = Callable[..., str | None]
ShortDrafter = Callable[..., dict[str, Verdict]]
AnswerChecker = Callable[..., dict[str, Judgement]]

LetterDrafter = Callable[..., LetterSlots | None]
LetterRenderer = Callable[..., Path]


@dataclass
class SessionContext:
    api: WorkerApi
    run_id: str
    ats: str  # a label for the fill events and failures, never behavior
    browser: VisibleBrowser
    page: Page
    console_errors: list[str]
    knowledge: Knowledge
    resume_json: dict[str, Any]
    resume_pdf: Path
    company: str
    title: str
    description: str | None
    # The career record, so a fact set mid-application can rebuild what is known.
    career: dict[str, Any] | None = None
    transcript_pdf: Path | None = None
    dry_run: bool = False
    drafter: Drafter = draft_contextual_answer
    short_drafter: ShortDrafter = draft_short_answers
    checker: AnswerChecker = check_answers
    letter_drafter: LetterDrafter = draft_slots
    letter_template: Callable[[], LetterTemplate] = load_template
    editor: Callable[[Path], None] = edit_in_vscode
    letter_renderer: LetterRenderer = letter_pdf
    letter_dir: Path | None = None  # where the editable letter and its PDF live
    use_claude: bool = True
    # Retry a field that fails right here, on the live page (design R55).
    repair: bool = False
    # Print every control's whole decision; with `step`, pause before each fill.
    explain: bool = False
    step: bool = False
    # Ask for a missing fact's value at the terminal instead of only reporting it.
    ask_facts: bool = False
    # How many times the audit may look, repair, and look again on one step.
    audit_rounds: int = 2
    poll_seconds: float = 1.0
    keys: KeyWatcher | NoKeys = field(default_factory=KeyWatcher)
    prompt: Callable[[str], str] = input


@dataclass
class SessionState:
    review: list[tuple[str, str]] = field(default_factory=list)  # (question, source) long-form
    cover_letter: str | None = None  # the edited letter, once per run
    collected: dict[str, dict[str, Any]] = field(default_factory=dict)
    seeds: dict[str, tuple[str, str]] = field(default_factory=dict)  # question -> (text, source)
    planned: dict[str, str] = field(default_factory=dict)  # question -> planned value
    resolutions: dict[str, AnswerResolution] = field(default_factory=dict)
    announced: dict[str, str] = field(default_factory=dict)  # control key -> value shown
    observations: list[dict[str, Any]] = field(default_factory=list)
    persisted_observations: int = 0
    warnings: set[str] = field(default_factory=set)
    tier_answers: dict[str, str] = field(default_factory=dict)  # question -> pattern/model answer
    tier_source: dict[str, str] = field(default_factory=dict)  # question -> "pattern" | "model"
    tier_resolver: dict[str, str] = field(default_factory=dict)
    seed_resolver: dict[str, str] = field(default_factory=dict)  # question -> prose/model id
    asked_claude: dict[str, int] = field(default_factory=dict)  # question -> times asked
    judged: set[str] = field(default_factory=set)  # answers already checked against the facts
    repair_attempted: set[str] = field(default_factory=set)  # questions repair already retried
    budget: Budget = field(default_factory=Budget)
    misses: dict[str, Miss] = field(default_factory=dict)  # answer key -> why unanswered
    needed: dict[str, str] = field(default_factory=dict)  # question -> fact the model named
    gates_tried: set[str] = field(default_factory=set)  # gate questions already answered once
    gate_pending: dict[str, Any] | None = None  # a gate left for you, watched for your click
    step: int = 0
    form_seen: bool = False
    navigation: TabNavigation | None = None


def page_signature(page: Page, fields: list[dict[str, Any]]) -> tuple[str, tuple[str, ...]]:
    return page.url, tuple(str(item.get("fingerprint")) for item in fields)


def preview(value: str, *, limit: int = 100) -> str:
    """One terminal line of a value, however long the textarea answer is."""
    flat = " ".join(value.split())
    return flat if len(flat) <= limit else flat[: limit - 3].rstrip() + "..."


# A build that shells out can fail three ways: a bad exit (RuntimeError), no
# executable (FileNotFoundError, an OSError), or the 120 s cap
# (subprocess.TimeoutExpired, a SubprocessError). None of them is a DOM fault.
ARTIFACT_BUILD_ERRORS = (RuntimeError, OSError, subprocess.SubprocessError)


def error_note(error: BaseException, *, limit: int = 200) -> str:
    """The informative line of an exception message, not a tool's startup banner."""
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    text = lines[-1] if lines else error.__class__.__name__
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


TAG_WIDTH = 10
QUESTION_WIDTH = 42
VALUE_WIDTH = 60

# How many times one question may be put to the model in a run: the fill pass,
# and the audit once more after a repair or a new fact has changed something.
ASK_LIMIT = 2
# What one repair attempt costs the question's budget. A retry walks a control's
# strategies, or a fact's renderings across them, so it is never one operation.
REPAIR_COST = 5

# Passes over questions an answer revealed, before the page is left to you.
REVEAL_PASSES = 2

TAG_STYLES = {
    "pattern": "green",
    "claude": "magenta",
    "source": "dim",
    "consent": "dim",
    "long-form": "cyan",
    "prose": "cyan",
    "letter": "cyan",
    "picked up": "cyan",
    "saved": "green",
    "fact": "green",
    "kept": "dim",
    "left": "yellow",
    "optional": "dim",
    "review": "yellow",
    "failed": "red",
    "repaired": "green",
}


def echo(tag: str, question: str, value: str | None = None, *, note: str | None = None) -> None:
    """One aligned line: `  tag  question  -> value  (note)`."""
    style = TAG_STYLES.get(tag, "")
    label = f"[{style}]{tag:<{TAG_WIDTH}}[/{style}]" if style else f"{tag:<{TAG_WIDTH}}"
    text = preview(question, limit=QUESTION_WIDTH)
    line = f"  {label} {escape(text):<{QUESTION_WIDTH}}"
    if value is not None:
        line += f" -> {escape(preview(value, limit=VALUE_WIDTH))}"
    if note:
        line += f"  [dim]({escape(note)})[/dim]"
    console.print(line)


def _question_for(field: dict[str, Any]) -> str:
    """The question a control answers: its group for a choice control, else its label."""
    if str(field.get("type") or "").casefold() in {"radio", "checkbox"} and field.get("group"):
        return str(field.get("group")).strip()
    return str(field.get("label") or "").strip()



def member_control(
    fields: list[dict[str, Any]], field: dict[str, Any], value: str
) -> dict[str, Any] | None:
    """The control a value should operate: a choice group's matching option, else the field.

    A group's question is answered by one of its options, and `find_field`
    returns whichever option came first. Operating that one would check the
    wrong box. A checkbox group is left alone: correcting it means unchecking
    what is already ticked, which is yours to do.
    """
    field_type = str(field.get("type") or "").casefold()
    if field_type not in {"radio", "checkbox"}:
        return field
    group = normalize_question(str(field.get("group") or ""))
    members = [
        item
        for item in fields
        if str(item.get("type") or "").casefold() in {"radio", "checkbox"}
        and normalize_question(str(item.get("group") or "")) == group
    ]
    if field_type == "checkbox" and len(members) > 1:
        return None
    labels = [str(item.get("label") or item.get("value") or "") for item in members or [field]]
    chosen = choose_option(labels, value)
    if chosen is None:
        return None
    return (members or [field])[labels.index(chosen)]


def _claude_questions(fields: list[dict[str, Any]]) -> list[ShortQuestion]:
    """Unfilled, non-sensitive, non-prose, non-file controls left for Claude.

    Radio/checkbox groups with no option checked (options = the group's option
    labels), selects and typeaheads with a known menu (their options), and
    required text inputs (no options).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    questions: dict[str, ShortQuestion] = {}
    for item in fields:
        field_type = str(item.get("type") or "").casefold()
        tag = str(item.get("tag") or "").casefold()
        if field_type in {"file", "submit", "button", "reset", "hidden"} or tag == "button":
            continue
        if is_sensitive_field(item) or is_long_form_field(item):
            continue
        if field_type in {"radio", "checkbox"}:
            group = str(item.get("group") or "").strip()
            if group:
                groups.setdefault(group, []).append(item)
            continue
        if item.get("filled") is True:
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        if tag == "select" or item.get("options"):
            questions[label] = ShortQuestion(
                label, tuple(str(option) for option in item.get("options") or [])
            )
        elif item.get("required") is True:
            questions[label] = ShortQuestion(label, ())
    for group, members in groups.items():
        if any(member.get("filled") is True for member in members):
            continue
        options = tuple(
            str(member.get("label") or member.get("value") or "") for member in members
        )
        questions[group] = ShortQuestion(group, options)
    return list(questions.values())


def content_type_for(name: str) -> str:
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".html"):
        return "text/html"
    return "application/json"


class AutofillSession:
    def __init__(self, context: SessionContext) -> None:
        self.ctx = context
        self.state = SessionState()
        self.page = context.page
        self._explainer: Explainer | None = None
        self.timings = AutofillTimings(report=lambda line: console.print(line, style="dim"))

    # ----- reporting -------------------------------------------------------

    def upload_evidence(self, evidence: dict[str, bytes]) -> dict[str, str]:
        with self.timings.measure("evidence_upload", step=self.state.step):
            return self._upload_evidence(evidence)

    def _upload_evidence(self, evidence: dict[str, bytes]) -> dict[str, str]:
        """Upload what can be uploaded; evidence is never worth ending the session over."""
        paths: dict[str, str] = {}
        for name, content in evidence.items():
            try:
                paths[name] = self.ctx.api.upload_autofill_artifact(
                    self.ctx.run_id, name=name, content=content,
                    content_type=content_type_for(name),
                )
            except httpx.HTTPError as error:
                self.warn_once(
                    "evidence-upload",
                    f"Could not store failure evidence ({error.__class__.__name__}); "
                    "the fill carries on without it.",
                )
        return paths

    def report_page_failure(
        self, category: str, message: str, *, artifact_paths: dict[str, str] | None = None
    ) -> None:
        if artifact_paths is None:
            captured = inspect_fields(self.page)
            artifact_paths = self.upload_evidence(
                page_evidence(self.page, captured, console_errors=self.ctx.console_errors)
            )
        fingerprint = f"{category}|{self.page.url.split('?', 1)[0]}|{self.state.step}"
        self.ctx.api.persist_autofill_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "failures": [
                    {
                        "signature": failure_signature(
                            self.ctx.ats, category, fingerprint
                        ),
                        "category": category,
                        "field_fingerprint": fingerprint,
                        "error_message": message,
                        "artifact_paths": artifact_paths,
                    }
                ],
            },
        )

    def warn_once(self, key: str, message: str) -> None:
        if key not in self.state.warnings:
            console.print(f"[yellow]Warning:[/yellow] {message}")
            self.state.warnings.add(key)

    def sleep(self) -> None:
        pressed = self.ctx.keys.wait(self.ctx.poll_seconds)
        if "save" in pressed:
            self.save_now()
        if "done" in pressed:
            raise SubmittedByUser
        if "skip" in pressed:
            raise SkippedByUser

    def form_is_gone(self) -> bool:
        """The page has left the form for a confirmation, so nothing on it is ours."""
        try:
            fields = inspect_fields(self.page)
        except Error:
            return False
        return (
            classify_application_page(self.page, fields, self.state.navigation)
            == ApplicationPageState.SUBMITTED
        )

    def save_now(self) -> int:
        with self.timings.measure("history_checkpoint", step=self.state.step):
            return self._save_now()

    def _save_now(self) -> int:
        """Persist newly observed values for this run without approving reuse."""
        pending = self.state.observations[self.state.persisted_observations :]
        for offset in range(0, len(pending), 500):
            batch = pending[offset : offset + 500]
            self.ctx.api.persist_autofill_step(
                self.ctx.run_id,
                {
                    "step_number": self.state.step,
                    "url": self.page.url,
                    "observations": batch,
                },
            )
            self.state.persisted_observations += len(batch)
        console.print(f"  [dim]recorded {len(pending)} answer observation(s) for review[/dim]")
        return len(pending)

    # ----- form discovery --------------------------------------------------

    def wait_for_form(self) -> list[dict[str, Any]] | None:
        """Return the form's fields, or None once the page shows the application went in."""
        wall_reported = False
        unsettled_since: float | None = None
        while True:
            self.page = self.ctx.browser.active_page(self.page)
            fields = inspect_fields(self.page)
            if self.state.navigation is None:
                self.state.navigation = capture_tab_navigation(self.page)
            page_state = classify_application_page(self.page, fields, self.state.navigation)
            if page_state == ApplicationPageState.SUBMITTED:
                return None
            if page_state == ApplicationPageState.FORM:
                self.note_observed_gate()
                self.state.form_seen = True
                for item in fields:
                    item["ats"] = self.ctx.ats
                return fields
            if page_state == ApplicationPageState.OVERVIEW and self.state.form_seen:
                self.warn_once(
                    "overview-standby",
                    "Ashby Overview is open; standing by until you return to Application.",
                )
                self.sleep()
                continue
            if not self.state.form_seen:
                reveal_application_form(self.page)
                refreshed = inspect_fields(self.page)
                if (
                    classify_application_page(self.page, refreshed, self.state.navigation)
                    == ApplicationPageState.FORM
                ):
                    self.state.form_seen = True
                    for item in refreshed:
                        item["ats"] = self.ctx.ats
                    return refreshed
            if page_state == ApplicationPageState.LOADING:
                unsettled_since = unsettled_since or time.monotonic()
                if time.monotonic() - unsettled_since < 5:
                    self.sleep()
                    continue
            if self.pass_the_gate():
                continue
            if not wall_reported:
                self.report_page_failure(
                    "navigation_wall",
                    "No unambiguous Apply control revealed a recognizable form",
                )
                wall_reported = True
                console.print(
                    "[yellow]Hit a wall:[/yellow] I could not safely reach the application "
                    "form. Use the open browser to reach it; I am watching the page."
                )
            self.sleep()

    # ----- the gate between the posting and its form ------------------------

    def pass_the_gate(self) -> bool:
        """Answer a gate the way a fact says, and report whether the form opened.

        A gate is a choice like any other: the pattern you accepted answers it,
        else the model names which fact does. Nothing is clicked without a fact
        behind it, and the readback is the form itself appearing (design §10.2).
        """
        gates = inspect_gateways(self.page)
        if not gates:
            return False
        gate = gates[0]
        question = str(gate["label"])
        self.state.gate_pending = gate
        if question in self.state.gates_tried:
            return False
        self.state.gates_tried.add(question)
        resolution = Resolver(self.ctx.knowledge).resolve(gate)
        if resolution is None and self.ctx.use_claude:
            resolution = self.gate_from_claude(gate)
        if resolution is None:
            console.print(
                f"  [dim]a way in is being asked for ({', '.join(gate['options'])}); "
                "no fact answers it yet, so it is yours to click[/dim]"
            )
            return False
        clicked = click_gateway(self.page, gate, resolution.value)
        opened = form_appeared(self.page) if clicked else False
        self.record_gate(gate, resolution=resolution, clicked=clicked, opened=opened)
        if opened:
            echo(resolution.source, question, clicked, note=resolution.fact_key)
            self.state.gate_pending = None
        return opened

    def gate_from_claude(self, gate: dict[str, Any]) -> AnswerResolution | None:
        """One bounded pass: which fact says how you start an application."""
        question = str(gate["label"])
        console.print("  [dim]asking Claude which fact says how to start this application[/dim]")
        verdicts = self.ctx.short_drafter(
            [ShortQuestion(question, tuple(str(item) for item in gate["options"]))],
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
        )
        fits = {
            name: verdict for name, verdict in verdicts.items() if verdict.kind == "fits_fact"
        }
        for verdict in verdicts.values():
            if verdict.kind == "no_fact" and verdict.missing_fact:
                self.state.needed[verdict.question] = verdict.missing_fact
        if not fits:
            return None
        return VerdictResolver(fits, self.ctx.knowledge).resolve(gate)

    def record_gate(
        self,
        gate: dict[str, Any],
        *,
        resolution: AnswerResolution | None,
        clicked: str | None,
        opened: bool,
    ) -> None:
        """Write the gate as a fill event, so the question and its proposal are learned."""
        question = str(gate["label"])
        self.ctx.api.persist_autofill_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "fill_events": [
                    fill_event(
                        gate,
                        strategy="click_option_button",
                        stopped_at=None if opened else "strategy",
                        operated_ok=opened,
                        value=clicked,
                        resolution=resolution,
                        source=None if resolution is not None else "manual",
                    )
                ],
            },
        )
        if clicked and opened:
            self.state.tier_answers[question] = clicked
            self.state.tier_source[question] = (
                resolution.source if resolution is not None else "manual"
            )
            if resolution is not None:
                self.state.tier_resolver[question] = resolution.resolver_id
            self.state.observations.append(
                {
                    "event_index": len(self.state.observations),
                    "step_number": self.state.step,
                    "page_url": self.page.url,
                    "question": question,
                    "answer_text": clicked,
                    "source": self.state.tier_source[question],
                    "form": "short",
                    "control_kind": "choice_group",
                    "field_fingerprint": f"gate:{normalize_answer_question(question)}",
                    "resolver_id": resolution.resolver_id if resolution is not None else None,
                    "proposed_value": resolution.value if resolution is not None else None,
                }
            )

    def note_observed_gate(self) -> None:
        """Learn the way in you clicked yourself, so the next run does not ask."""
        gate = self.state.gate_pending
        self.state.gate_pending = None
        if gate is None:
            return
        clicked = observed_choice(self.page)
        if not clicked:
            return
        self.record_gate(gate, resolution=None, clicked=clicked, opened=True)
        echo("picked up", str(gate["label"]), clicked, note="bind it in the Inbox")

    def emit_warnings(self, fields: list[dict[str, Any]]) -> None:
        resume_fields = [item for item in fields if is_resume_field(item)]
        if len(resume_fields) > 1:
            self.warn_once(
                "multiple-resumes", "multiple resume upload controls; all are left manual."
            )
        if any(item.get("filled") is True for item in fields):
            self.warn_once("profile-drift", "the ATS already prefilled controls; review for drift.")
        knockout = ("sponsor", "authorized to work", "salary", "relocat", "security clearance")
        if any(
            marker in str(item.get("label") or item.get("group") or "").casefold()
            for marker in knockout
            for item in fields
        ):
            self.warn_once("knockout", "review factual or knockout questions carefully.")

    # ----- deterministic fill ---------------------------------------------

    def explainer(self) -> Explainer | None:
        """The `--explain` trace for one pass; it pauses on each fill with `--step`."""
        if not (self.ctx.explain or self.ctx.step):
            return None
        if self._explainer is None:
            self._explainer = Explainer(self.page, step=self.ctx.step)
        # `c` while stepping stops the pauses for the rest of the run.
        self._explainer.page = self.page
        return self._explainer

    def fill_revealed(self, fields: list[dict[str, Any]], result: dict[str, Any]) -> None:
        """Fill the questions an answer revealed, such as race after Hispanic/Latino.

        A form can add controls once a choice is made. The page is read again
        after the pass, and anything empty that was not there before gets its
        own pass, at most `REVEAL_PASSES` times, so a form that keeps growing
        cannot hold the run.
        """
        seen = {normalize_answer_question(question_of(item)) for item in fields}
        for _ in range(REVEAL_PASSES):
            new = {
                key
                for item in inspect_fields(self.page)
                if item.get("filled") is not True
                and (key := normalize_answer_question(question_of(item)))
                and key not in seen
            }
            if not new:
                return
            seen |= new
            more = self.fill_deterministic(fields, only=new)
            for name in ("filled_fields", "skipped_fields", "failures"):
                result.setdefault(name, []).extend(more.get(name) or [])

    def fill_deterministic(
        self, fields: list[dict[str, Any]], *, only: set[str] | None = None
    ) -> dict[str, Any]:
        """One deterministic pass; `only` limits it to these question keys."""
        resolver = Resolver(self.ctx.knowledge)
        fields[:], plan = plan_autofill(self.page, resolver=resolver)
        self.state.misses.update(resolver.misses)
        for item in fields:
            item["ats"] = self.ctx.ats

        def in_scope(item: dict[str, Any]) -> bool:
            return only is None or normalize_answer_question(question_of(item)) in only

        if only is not None:
            plan = type(plan)(
                tuple(fill for fill in plan.fills if in_scope(fields[fill.index])),
                tuple(
                    question for question in plan.unanswered_required_questions
                    if normalize_answer_question(question) in only
                ),
                plan.reasons,
            )
        if not self.ctx.dry_run:
            # Prose controls are seeded by fill_long_form, which knows their source.
            plan = type(plan)(
                tuple(
                    fill for fill in plan.fills
                    if not is_long_form_field(fields[fill.index])
                ),
                plan.unanswered_required_questions,
                plan.reasons | {
                    fill.index: "long-form: seeded from approved prose, else a Claude draft"
                    for fill in plan.fills
                    if is_long_form_field(fields[fill.index])
                },
            )
        if only is None:
            console.print()
            console.rule(f"[bold]Step {self.state.step + 1}[/bold]", align="left")
            console.print(
                f"  {len(fields)} controls · {len(plan.fills)} planned deterministically · "
                f"{len(plan.unanswered_required_questions)} required still open"
            )
        else:
            console.print(f"  [dim]{len(only)} question(s) appeared after answering; "
                          f"{len(plan.fills)} planned[/dim]")
        explainer = self.explainer()
        if explainer is not None:
            explainer.begin(
                f"step {self.state.step + 1}, "
                + ("patterns and engine" if only is None else "revealed questions"),
                fields,
                plan,
                resolver,
                scope=in_scope,
            )
        if self.ctx.dry_run:
            if explainer is not None:
                explainer.dry_run()
            self.ctx.api.persist_autofill_step(
                self.ctx.run_id,
                {
                    "step_number": self.state.step,
                    "url": self.page.url,
                    "fill_events": planned_events(fields, plan, misses=self.state.misses),
                },
            )
            return {"filled_fields": [], "skipped_fields": [], "failures": [], "plan": plan}
        before = page_evidence(self.page, fields, console_errors=self.ctx.console_errors)
        result = apply_autofill(
            self.page,
            fields,
            plan,
            resume_path=self.ctx.resume_pdf,
            transcript_path=self.ctx.transcript_pdf,
            assert_active=lambda: None,
            preferences=self.ctx.knowledge.preferences,
            observer=explainer,
        )
        if explainer is not None:
            explainer.finish()
        for fill in plan.fills:
            question = _question_for(fields[fill.index])
            applied = result["applied"].get(fill.index)
            if applied is not None and question:
                self.state.planned[question] = applied
                if fill.resolution is not None:
                    field = fields[fill.index]
                    key = f"{self.state.step}:{field.get('fingerprint') or ''}#{fill.index}"
                    self.state.resolutions[key] = AnswerResolution(
                        applied, fill.resolution.source, fill.resolution.resolver_id
                    )
                if fill.chooser is not None:
                    echo(fill.chooser, question, applied)
                elif fill.resolution is not None:
                    self.state.tier_answers[question] = applied
                    self.state.tier_source[question] = fill.resolution.source
                    self.state.tier_resolver[question] = fill.resolution.resolver_id
                    if fill.resolution.resolver_id.startswith("engine:"):
                        echo("source", question, applied)
                    else:
                        echo(fill.resolution.source, question, applied,
                             note=fill.resolution.fact_key)
        for question in result["unanswered_required_questions"]:
            item = next(
                (
                    candidate
                    for candidate in fields
                    if str(candidate.get("label") or candidate.get("group") or "") == question
                ),
                {},
            )
            fingerprint = str(item.get("fingerprint") or question)
            result["failures"].append(
                {
                    "signature": failure_signature(
                        self.ctx.ats, "required_field_empty", fingerprint
                    ),
                    "category": "required_field_empty",
                    "field_label": question,
                    "control_kind": item.get("type") or item.get("tag"),
                    "field_fingerprint": fingerprint,
                    "error_message": "Required field remained empty after deterministic planning",
                    "dom_signals": {
                        key: item.get(key)
                        for key in ("tag", "type", "name", "label", "group", "role")
                    },
                }
            )
        after = page_evidence(
            self.page, inspect_fields(self.page), console_errors=self.ctx.console_errors
        )
        after["fill-plan.json"] = json.dumps(
            [
                {
                    "field": fields[fill.index].get("fingerprint"),
                    "kind": fill.kind,
                    "has_value": fill.value is not None,
                }
                for fill in plan.fills
            ],
            indent=2,
        ).encode()
        after["verification.json"] = json.dumps(
            {key: result[key] for key in ("filled_fields", "skipped_fields", "failures")}
            | {"unanswered_required_questions": result["unanswered_required_questions"]}
            | {"submitted": False},
            indent=2,
            default=str,
        ).encode()
        self.evidence = {**before, **{f"after-{k}": v for k, v in after.items()}}
        artifact_paths: dict[str, str] = {}
        # A field left for the user is expected; only a fill that went wrong
        # earns the page evidence, which is twenty uploads.
        if any(failure["category"] != "required_field_empty" for failure in result["failures"]):
            artifact_paths = self.upload_evidence(self.evidence)
            for failure in result["failures"]:
                failure["artifact_paths"] = artifact_paths
        issues = self.ctx.api.persist_autofill_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "filled_count": len(result["filled_fields"]),
                "skipped_count": len(result["skipped_fields"]),
                "failures": result["failures"],
                # Prose and cover letter controls are filled by their own paths.
                "fill_events": result["events"] + unresolved_events(
                    fields,
                    plan,
                    skip=lambda item: is_long_form_field(item)
                    or is_cover_letter_field(item)
                    or not in_scope(item),
                    misses=self.state.misses,
                ),
            },
        )
        result["artifact_paths"] = artifact_paths
        if self.ctx.repair:
            self.repair_in_place(fields, result["failures"], issues)
        return result

    def repair_in_place(
        self,
        fields: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        issues: list[dict[str, Any]],
    ) -> None:
        """Retry each field that stopped at a format or a strategy, on this page.

        Any site works the same way, because the retry is about the control, not
        the board. What reads back fills the field now and is stored on its
        failure as a repaired candidate; `os hunt validate` decides whether it
        becomes the rule. Never touches a control that reads as submitting.
        """
        by_signature = {str(issue.get("signature")): issue for issue in issues}
        for failure in failures:
            stage = failure.get("stopped_at")
            issue = by_signature.get(str(failure.get("signature")))
            if stage not in {"format", "strategy"} or issue is None:
                continue
            question = str(failure.get("question") or failure.get("field_label") or "")
            field = find_field(fields, question)
            if field is None:
                continue
            # The audit must not re-queue what has already been retried here.
            self.state.repair_attempted.add(normalize_answer_question(question))
            self.state.budget.charge(question, REPAIR_COST)
            try:
                if stage == "format" and failure.get("fact_key"):
                    candidate = repair_format(
                        self.page, field, self.ctx.knowledge, str(failure["fact_key"]),
                        failed_formatter=failure.get("formatter"),
                    )
                elif stage == "strategy" and failure.get("intended_value"):
                    candidate = repair_strategy(
                        self.page, field, str(failure["intended_value"])
                    )
                else:
                    candidate = None
            except (Error, RuntimeError):
                candidate = None
            if candidate is None:
                echo("failed", question, note=f"{stage}: nothing else took; left for you")
                continue
            self.ctx.api.update_autofill_failure(
                str(issue["id"]), {"status": "repaired", "candidate": candidate}
            )
            rule = candidate.get("strategy") if stage == "strategy" else candidate.get("formatter")
            echo("repaired", question, str(candidate.get("value") or ""), note=f"{stage}: {rule}")

    # ----- claude tier -------------------------------------------------------

    def fill_with_claude(self, fields: list[dict[str, Any]], result: dict[str, Any]) -> None:
        """One bounded pass asking Claude about whatever is still unfilled."""
        current = inspect_fields(self.page)
        # A typeahead hides its choices until opened; Claude should pick from
        # them verbatim rather than guess at the wording.
        pending = [
            item
            for item in current
            if item.get("role") == "combobox"
            and item.get("filled") is not True
            and not is_sensitive_field(item)
        ]
        if pending:
            console.print(f"  [dim]reading the options of {len(pending)} dropdown(s)[/dim]")
        for item in pending:
            try:
                item["options"] = combobox_options(self.page, fresh_control(self.page, item))
            except (Error, RuntimeError):
                item["options"] = []
                if self.form_is_gone():
                    return
        # A question is put to the model at most ASK_LIMIT times in a run, so a
        # re-render that restarts the step does not ask again, while the audit
        # may ask a second time once a new fact has appeared.
        questions = [
            question
            for question in _claude_questions(current)
            if self.may_ask(question.question)
        ]
        if not questions:
            return
        for pending_question in questions:
            self.note_asked(pending_question.question)
        console.print(f"  [dim]asking Claude which fact answers {len(questions)} question(s)[/dim]")
        verdicts = self.ctx.short_drafter(
            questions,
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
        )
        for verdict in verdicts.values():
            if verdict.kind == "no_fact" and verdict.missing_fact:
                self.state.needed[verdict.question] = verdict.missing_fact
        fits = {question: item for question, item in verdicts.items() if item.kind == "fits_fact"}
        if not fits:
            console.print("  [dim]Claude found no fact that answers them[/dim]")
            return
        if self.form_is_gone():
            return
        resolver = VerdictResolver(fits, self.ctx.knowledge)
        claude_fields, plan = plan_autofill(self.page, resolver=resolver)
        self.state.misses.update(resolver.misses)
        explainer = self.explainer()
        if explainer is not None:
            explainer.begin(
                f"step {self.state.step + 1}, the model's facts", claude_fields, plan, resolver
            )
        claude_result = apply_autofill(
            self.page, claude_fields, plan, resume_path=None, assert_active=lambda: None,
            preferences=self.ctx.knowledge.preferences, observer=explainer,
        )
        for fill in plan.fills:
            question = _question_for(claude_fields[fill.index])
            applied = claude_result["applied"].get(fill.index)
            if applied is not None and question in fits and fill.resolution is not None:
                self.state.tier_answers[question] = applied
                self.state.tier_source[question] = "model"
                self.state.tier_resolver[question] = fill.resolution.resolver_id
                control = claude_fields[fill.index]
                key = f"{self.state.step}:{control.get('fingerprint') or ''}#{fill.index}"
                self.state.resolutions[key] = AnswerResolution(
                    applied, "model", fill.resolution.resolver_id,
                    fact_key=fill.resolution.fact_key, formatter=fill.resolution.formatter,
                )
                echo("claude", question, applied, note=fill.resolution.fact_key)
        result["failures"].extend(claude_result["failures"])
        if claude_result["events"]:
            self.ctx.api.persist_autofill_step(
                self.ctx.run_id,
                {
                    "step_number": self.state.step,
                    "url": self.page.url,
                    "fill_events": claude_result["events"],
                },
            )

    # ----- long-form drafts ------------------------------------------------

    def references(self, question: str) -> list[dict[str, Any]]:
        """Your approved writing on similar questions; the archive is never a suggestion."""
        writing = [
            {"question": item.get("question_label"), "answer_text": item.get("text")}
            for item in self.ctx.knowledge.prose
        ]
        return similar_answers(question, writing)

    def seed_for(self, question: str, *, limit: int | None = None) -> tuple[str, str, str, str]:
        """Return (seed text, seed source, resolver id, human note)."""
        key = normalize_answer_question(question)
        approved = next(
            (
                item
                for item in self.ctx.knowledge.prose
                if normalize_answer_question(str(item.get("question_label") or "")) == key
                and str(item.get("text") or "").strip()
            ),
            None,
        )
        if approved is not None:
            return (
                str(approved["text"]).strip(),
                "prose",
                f"prose:{approved.get('id')}",
                "approved prose (edit freely)",
            )
        if not self.ctx.use_claude:
            return "", "empty", "", "drafting disabled; write it, it is saved when submitted"
        draft = self.ctx.drafter(
            question=question,
            base=None,
            posting={
                "company": self.ctx.company,
                "role": self.ctx.title,
                "description": self.ctx.description,
            },
            resume=self.ctx.resume_json,
            facts=self.ctx.knowledge.values(),
            references=self.references(question),
            limit=limit,
        )
        if draft:
            return draft, "model", "model:long_form_v1", "claude draft tailored to this posting"
        return "", "empty", "", "no approved prose and no claude draft; write from scratch"

    def fill_long_form(self, fields: list[dict[str, Any]]) -> None:
        """Seed every prose control with its best answer and say which deserve a look.

        An exact library answer goes in as is. Otherwise a Claude draft grounded
        in the posting goes in and is flagged for review. A control that already
        holds text is kept, and one with nothing to offer is left alone.
        """
        for item in fields:
            if not is_long_form_field(item):
                continue
            if is_cover_letter_field(item):
                continue  # fill_cover_letter owns it
            question = str(item.get("label") or "").strip()
            if not question:
                continue
            if item.get("filled") is True:
                echo("kept", question, note="already has text")
                continue
            limit = item.get("max_length")
            seed, source, resolver_id, note = self.seed_for(question, limit=limit)
            if seed and limit is not None and len(seed) > int(limit):
                # Writing past the cap registers and reads back, but the form
                # rejects it on submit. Say so rather than send it.
                echo(
                    "left",
                    question,
                    note=f"best answer runs {len(seed)} characters; the form caps it at {limit}",
                )
                continue
            self.state.seeds[question] = (seed, source)
            self.state.seed_resolver[question] = resolver_id
            if not seed:
                echo("left", question, note=note.split(";", 1)[0])
                continue
            try:
                set_registered_value(fresh_control(self.page, item), seed)
                ok = verify_control(self.page, item, seed)
            except (Error, RuntimeError) as error:
                ok = False
                echo("failed", question, note=str(error))
            if not ok:
                self.report_page_failure(
                    "readback_mismatch", f"Long-form answer for {question!r} did not register"
                )
                continue
            key = f"{self.state.step}:{item.get('fingerprint') or ''}#{item['index']}"
            self.state.resolutions[key] = AnswerResolution(seed, source, resolver_id)
            self.state.announced[key] = seed
            label = "claude draft" if source == "model" else "prose"
            echo("long-form", question, seed, note=label)
            if source == "model":
                self.state.review.append((question, label))

    # ----- cover letter ----------------------------------------------------

    def letter_directory(self) -> Path:
        if self.ctx.letter_dir is not None:
            return self.ctx.letter_dir
        from personal_os_worker.core.service import state_dir

        return state_dir() / "cover-letters" / self.ctx.run_id

    def cover_letter_text(self) -> str:
        """Draft the slots once, open the letter in VS Code, and keep what the user left."""
        if self.state.cover_letter is not None:
            return self.state.cover_letter
        template = self.ctx.letter_template()
        posting = {
            "company": self.ctx.company,
            "role": self.ctx.title,
            "description": self.ctx.description,
        }
        slots = None
        if self.ctx.use_claude:
            console.print("  [dim]drafting the cover letter slots[/dim]")
            slots = self.ctx.letter_drafter(template, posting=posting, resume=self.ctx.resume_json)
        if slots is None:
            slots = placeholder_slots(template)
            note = "slots left as [prompts]; fill them in"
        else:
            note = f"{slots.primary_module}" + (
                f" + {slots.secondary_module}" if slots.secondary_module else ""
            )
        text = render_letter(template, slots, company=self.ctx.company, role=self.ctx.title)
        directory = self.letter_directory()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "cover_letter.md"
        path.write_text(text, encoding="utf-8")
        echo("letter", "Cover letter", note=note)
        console.print("  [bold]Cover letter open in VS Code[/bold] — close the tab to continue.")
        self.ctx.editor(path)
        self.state.cover_letter = path.read_text(encoding="utf-8").strip() + "\n"
        return self.state.cover_letter

    def fill_cover_letter(self, fields: list[dict[str, Any]]) -> None:
        """Fill every cover-letter control on the page: textareas get the text, uploads the PDF."""
        targets = [
            item
            for item in fields
            if is_cover_letter_field(item) and item.get("filled") is not True
        ]
        if not targets:
            return
        text = self.cover_letter_text()
        if not text.strip():
            echo("left", "Cover letter", note="the file was empty")
            return
        for item in targets:
            question = str(item.get("label") or "Cover letter").strip()
            upload = str(item.get("type") or "").casefold() == "file"
            pdf: Path | None = None
            if upload:
                # Rendering the PDF is not a browser action. Keep its failure
                # apart from a readback mismatch: "I never produced a file" and
                # "the DOM refused my value" need different responses.
                try:
                    pdf = self.ctx.letter_renderer(
                        self.ctx.resume_json.get("contact", {}),
                        text,
                        self.letter_directory(),
                        company=self.ctx.company,
                    )
                except ARTIFACT_BUILD_ERRORS as error:
                    note = error_note(error)
                    echo("failed", question, note=note)
                    self.report_page_failure(
                        "artifact_build_failed",
                        f"Cover letter PDF for {question!r} was never built: {note}",
                    )
                    continue
            shown = ""
            try:
                if pdf is not None:
                    fresh_control(self.page, item).set_input_files(str(pdf))
                    self.page.wait_for_timeout(750)
                    ok = verify_control(self.page, item, None, resume=True, filename=pdf.name)
                    shown = pdf.name
                else:
                    set_registered_value(fresh_control(self.page, item), text)
                    ok = verify_control(self.page, item, text)
                    shown = text.strip()
                    self.state.seeds[question] = (shown, "model")
            except (Error, RuntimeError) as error:
                ok = False
                echo("failed", question, note=error_note(error))
            if ok:
                letter_key = f"{self.state.step}:{item.get('fingerprint') or ''}#{item['index']}"
                self.state.announced[letter_key] = shown
                echo("letter", question, shown)
            else:
                self.report_page_failure(
                    "readback_mismatch", f"Cover letter for {question!r} did not register"
                )

    # ----- the audit, and the repairs it drives ----------------------------

    def may_ask(self, question: str) -> bool:
        return self.state.asked_claude.get(question, 0) < ASK_LIMIT

    def note_asked(self, question: str) -> None:
        self.state.asked_claude[question] = self.state.asked_claude.get(question, 0) + 1

    def options_for(self, fields: list[dict[str, Any]], field: dict[str, Any]) -> list[str]:
        """The options the question offers, gathered the way the planner gathers them."""
        return group_options(fields, field)

    def filled_answers(self, fields: list[dict[str, Any]]) -> list[FilledAnswer]:
        """Every short answer the page holds, with who put it there.

        Prose, cover letters, consent, signatures, and uploads are excluded: a
        fact cannot judge them.
        """
        try:
            entries = snapshot_entries(self.page, fields)
        except Error:
            return []
        by_question: dict[str, dict[str, Any]] = {}
        for item in fields:
            question = _question_for(item)
            if question:
                by_question.setdefault(question, item)
        answers: list[FilledAnswer] = []
        for entry in entries:
            question = str(entry["question"])
            value = str(entry["answer_text"]).strip()
            control = by_question.get(question)
            if not value or control is None or entry.get("form") == "long":
                continue
            if (
                is_sensitive_field(control)
                or is_long_form_field(control)
                or is_cover_letter_field(control)
            ):
                continue
            source = self.state.tier_source.get(question)
            if source is None:
                seed, seed_source = self.state.seeds.get(question, ("", "empty"))
                source = (
                    seed_source
                    if seed_source in {"prose", "model"} and value == seed.strip()
                    else "manual"
                )
            answers.append(
                FilledAnswer(
                    question, value, tuple(self.options_for(fields, control)), source
                )
            )
        return answers

    def wrong_answers(self, fields: list[dict[str, Any]]) -> list[Finding]:
        """Answers a fact says are wrong, judged once per answer per run."""
        if not self.ctx.use_claude:
            return []
        fresh = [
            answer
            for answer in self.filled_answers(fields)
            if answer.question not in self.state.judged
        ]
        if not fresh:
            return []
        self.state.judged.update(answer.question for answer in fresh)
        console.print(f"  [dim]checking {len(fresh)} answer(s) against the facts[/dim]")
        judgements = self.ctx.checker(
            fresh,
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
        )
        held = {answer.question: answer for answer in fresh}
        findings: list[Finding] = []
        for judgement in judgements.values():
            answer = held.get(judgement.question)
            if judgement.kind != "wrong" or not judgement.fact_key or answer is None:
                continue
            findings.append(
                Finding(
                    judgement.question,
                    "wrong_answer",
                    required=False,
                    note=judgement.note or f"{judgement.fact_key} says otherwise",
                    fact_key=judgement.fact_key,
                    answer=answer.answer,
                    source=answer.source,
                )
            )
        return findings

    def audit(
        self, fields: list[dict[str, Any]], failures: list[dict[str, Any]]
    ) -> list[Finding]:
        """What is still wrong with this page: nothing answered it, or a fact disagrees."""
        try:
            current = inspect_fields(self.page)
            open_questions = audit_filled_form(current)
        except Error:
            return []
        by_question = {
            normalize_answer_question(str(failure.get("question") or failure.get("field_label")
                                          or "")): failure
            for failure in failures
        }
        findings = [
            classify_empty(
                question,
                required=required,
                needed=self.state.needed,
                misses=self.state.misses,
                failures=by_question,
            )
            for question, required in (
                [(item, True) for item in open_questions["required_unfilled"]]
                + [(item, False) for item in open_questions["optional_unfilled"]]
            )
        ]
        return order(findings + self.wrong_answers(fields))

    def audit_and_repair(
        self, fields: list[dict[str, Any]], result: dict[str, Any]
    ) -> None:
        """Look, fix what has a path, look again, then report whatever is left.

        The repairs are silent: each one echoes the line it changed, and only
        what no lane could fix is listed for you at the end.
        """
        failures = list(result.get("failures") or [])
        findings = self.audit(fields, failures)
        for _round in range(max(1, self.ctx.audit_rounds)):
            if not findings:
                break
            if not self.remediate(fields, findings):
                break
            findings = self.audit(fields, failures)
        self.report_open(findings)

    def remediate(self, fields: list[dict[str, Any]], findings: list[Finding]) -> bool:
        """Run each finding's repair path; True when anything on the page changed."""
        acted = False
        if self.ctx.repair:
            acted = self.queue_repairs(fields, findings) or acted
        acted = self.ask_again(fields, findings) or acted
        for finding in findings:
            if finding.cause == "wrong_answer":
                acted = self.correct_answer(fields, finding) or acted
            elif finding.cause == "fact_gap" and self.ctx.ask_facts:
                acted = self.ask_for_fact(fields, finding) or acted
        return acted

    def queue_repairs(self, fields: list[dict[str, Any]], findings: list[Finding]) -> bool:
        """Record the audit's own failures, then let the repair lane retry them here.

        A question the live fill already retried is not retried again; what the
        audit adds is the field no fill event ever failed on, because no tier
        produced a value for it at all.
        """
        failures: list[dict[str, Any]] = []
        for finding in findings:
            if finding.cause not in REPAIRABLE_CAUSES:
                continue
            key = normalize_answer_question(finding.question)
            field = find_field(fields, finding.question)
            if field is None or key in self.state.repair_attempted:
                continue
            if not self.state.budget.allows(finding.question):
                continue
            category = (
                "format_miss" if finding.cause == "format_error" else "strategy_miss"
            )
            stage = "format" if finding.cause == "format_error" else "strategy"
            fingerprint = str(field.get("fingerprint") or finding.question)
            failures.append(
                {
                    "signature": failure_signature(self.ctx.ats, category, fingerprint),
                    "category": category,
                    "stopped_at": stage,
                    "question": finding.question,
                    "field_label": field.get("label") or finding.question,
                    "control_kind": field.get("type") or field.get("tag"),
                    "field_fingerprint": fingerprint,
                    "fact_key": finding.fact_key,
                    "formatter": finding.formatter,
                    "intended_value": finding.intended_value,
                    "error_message": finding.note or "the audit found this field empty",
                    "dom_signals": {
                        name: field.get(name)
                        for name in ("tag", "type", "name", "label", "group", "role")
                    },
                }
            )
        if not failures:
            return False
        issues = self.ctx.api.persist_autofill_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "failures": failures,
            },
        )
        self.repair_in_place(fields, failures, issues)
        return True

    def ask_again(self, fields: list[dict[str, Any]], findings: list[Finding]) -> bool:
        """Put every unanswered question back to the model, once more, with the options."""
        if not self.ctx.use_claude:
            return False
        questions: list[ShortQuestion] = []
        targets: dict[str, dict[str, Any]] = {}
        for finding in findings:
            if finding.cause not in {"pattern_gap", "fact_gap"}:
                continue
            field = find_field(fields, finding.question)
            if field is None or not self.may_ask(finding.question):
                continue
            targets[finding.question] = field
            questions.append(
                ShortQuestion(
                    finding.question, tuple(self.options_for(fields, field))
                )
            )
        if not questions:
            return False
        for pending_question in questions:
            self.note_asked(pending_question.question)
        console.print(
            f"  [dim]asking Claude again about {len(questions)} unanswered question(s)[/dim]"
        )
        verdicts = self.ctx.short_drafter(
            questions,
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
        )
        fits = {
            name: verdict for name, verdict in verdicts.items() if verdict.kind == "fits_fact"
        }
        for verdict in verdicts.values():
            if verdict.kind == "no_fact" and verdict.missing_fact:
                self.state.needed[verdict.question] = verdict.missing_fact
        if not fits:
            return False
        resolver = VerdictResolver(fits, self.ctx.knowledge)
        acted = False
        for question, field in targets.items():
            if question not in fits:
                continue
            resolution = resolver.resolve(
                dict(field) | {"label": question, "options": self.options_for(fields, field)}
            )
            if resolution is None:
                continue
            shown = self.apply_value(
                fields,
                field,
                resolution.value,
                source="model",
                resolver_id=resolution.resolver_id,
                fact_key=resolution.fact_key,
                formatter=resolution.formatter,
            )
            if shown is not None:
                echo("claude", question, shown, note=resolution.fact_key)
                acted = True
        self.state.misses.update(resolver.misses)
        return acted

    def correct_answer(self, fields: list[dict[str, Any]], finding: Finding) -> bool:
        """Re-render the fact that contradicts an answer the worker itself filled."""
        key = str(finding.fact_key or "")
        if key not in self.ctx.knowledge.facts:
            return False
        if finding.source not in WORKER_SOURCES:
            echo(
                "review",
                finding.question,
                finding.answer,
                note=f"{key} says otherwise ({finding.note}); yours to change",
            )
            return False
        field = find_field(fields, finding.question)
        if field is None:
            return False
        rendering = render(
            key,
            self.ctx.knowledge.facts[key],
            self.ctx.knowledge.types.get(key, "text"),
            field=dict(field) | {"options": self.options_for(fields, field)},
            question=finding.question,
            seed=self.ctx.knowledge.seed,
        )
        if rendering is None or rendering.value == finding.answer:
            return False
        shown = self.apply_value(
            fields,
            field,
            rendering.value,
            source="model",
            resolver_id="model:answer_check_v1",
            fact_key=key,
            formatter=rendering.formatter,
        )
        if shown is None:
            return False
        echo("repaired", finding.question, shown, note=f"{key} said {finding.note or 'otherwise'}")
        return True

    def ask_for_fact(self, fields: list[dict[str, Any]], finding: Finding) -> bool:
        """Ask at the terminal for the fact this question needs, then fill it in."""
        from personal_os_worker.jobs.facts.catalog import entry
        from personal_os_worker.jobs.facts.commands import VALUE_TYPES, parse_value

        key = str(finding.fact_key or "")
        console.print()
        console.print(f"  [bold]{escape(finding.question)}[/bold]")
        if not key:
            console.print(f"  needs a fact: {finding.missing_fact}", style="dim")
            key = self.ctx.prompt("  fact key (blank to skip): ").strip()
            if not key:
                return False
        known = entry(key)
        label = known.label if known else self.ctx.prompt("  label: ").strip()
        value_type = (
            known.value_type
            if known
            else self.ctx.prompt(f"  type ({', '.join(VALUE_TYPES)}): ").strip()
        )
        if not label or value_type not in VALUE_TYPES:
            console.print("  a fact needs a label and a type; skipped", style="yellow")
            return False
        raw = self.ctx.prompt(f"  value ({value_type}), blank to skip: ").strip()
        if not raw:
            return False
        try:
            value = parse_value(str(value_type), raw)
        except ValueError as error:
            console.print(f"  {error}", style="red")
            return False
        self.ctx.api.set_fact(key, value, label=label, value_type=str(value_type))
        self.refresh_knowledge()
        echo("fact", finding.question, str(value), note=f"set {key}")
        field = find_field(fields, finding.question)
        if field is None:
            return True
        rendering = render(
            key,
            self.ctx.knowledge.facts.get(key),
            self.ctx.knowledge.types.get(key, str(value_type)),
            field=dict(field) | {"options": self.options_for(fields, field)},
            question=finding.question,
            seed=self.ctx.knowledge.seed,
        )
        if rendering is None:
            return True
        shown = self.apply_value(
            fields,
            field,
            rendering.value,
            source="model",
            resolver_id="model:fact_v1",
            fact_key=key,
            formatter=rendering.formatter,
        )
        if shown is not None:
            echo("claude", finding.question, shown, note=key)
        return True

    def refresh_knowledge(self) -> None:
        """Rebuild what the run knows after a fact was set, keeping its preferences."""
        rebuilt = Knowledge.build(
            facts=self.ctx.api.facts(),
            career=self.ctx.career,
            patterns=self.ctx.knowledge.patterns,
            prose=self.ctx.knowledge.prose,
            company=self.ctx.company,
            seed=self.ctx.knowledge.seed,
        )
        self.ctx.knowledge = replace(rebuilt, preferences=self.ctx.knowledge.preferences)

    def apply_value(
        self,
        fields: list[dict[str, Any]],
        field: dict[str, Any],
        value: str,
        *,
        source: str,
        resolver_id: str,
        fact_key: str | None = None,
        formatter: str | None = None,
    ) -> str | None:
        """Set the control this value answers, read it back, and record the attempt.

        Every strategy the control kind supports is tried, so this is also how a
        repair reaches a widget the first pass could not drive.
        """
        question = _question_for(field)
        spent = self.state.budget.exhausted(question)
        if spent is not None:
            echo("left", question, note=spent)
            return None
        target = member_control(fields, field, value)
        if target is None:
            echo("left", question, note=f"no option answers {value!r}")
            return None
        field = target
        self.state.budget.charge(question, REPAIR_COST)
        try:
            candidate = repair_strategy(self.page, field, value)
        except (Error, RuntimeError):
            candidate = None
        shown = str(candidate.get("value") or "") if candidate is not None else ""
        resolution = AnswerResolution(
            shown or value, source, resolver_id, fact_key=fact_key, formatter=formatter
        )
        self.ctx.api.persist_autofill_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "fill_events": [
                    fill_event(
                        field,
                        strategy=str(candidate.get("strategy")) if candidate else None,
                        stopped_at=None if candidate else "strategy",
                        operated_ok=candidate is not None,
                        value=shown or value,
                        resolution=resolution,
                    )
                ],
            },
        )
        if candidate is None:
            return None
        key = f"{self.state.step}:{field.get('fingerprint') or ''}#{field.get('index')}"
        self.state.resolutions[key] = resolution
        self.state.announced[key] = shown
        self.state.tier_answers[question] = shown
        self.state.tier_source[question] = source
        self.state.tier_resolver[question] = resolver_id
        return shown

    def report_open(self, findings: list[Finding]) -> None:
        """What the audit could not fix, required first, outlined on the page.

        Optional fields count: nothing is left blank unless you leave it. Each
        line says the cause, so what you do about it is never a guess.
        """
        if not findings:
            console.print("  [dim]every field is filled, and no fact disagrees[/dim]")
            return
        with contextlib.suppress(Error):
            highlight_unfilled(self.page)
        empty = [item for item in findings if item.cause != "wrong_answer"]
        wrong = [item for item in findings if item.cause == "wrong_answer"]
        required = sum(1 for item in empty if item.required)
        console.print()
        if empty:
            console.print(
                f"  [bold]Left for you[/bold] ({required} required, "
                f"{len(empty) - required} optional, outlined on the page): "
                f"[dim]{summarize(empty)}[/dim]"
            )
            for item in empty:
                echo(
                    "left" if item.required else "optional",
                    item.question,
                    note=f"{item.cause.replace('_', ' ')}: {item.note}" if item.note
                    else item.cause.replace("_", " "),
                )
        if wrong:
            console.print()
            console.print("  [bold]A fact disagrees with these answers[/bold]:")
            for item in wrong:
                echo("review", item.question, item.answer, note=f"{item.fact_key}: {item.note}")

    def report_review(self) -> None:
        """List the long-form answers that came from a draft, once per step."""
        if not self.state.review:
            return
        console.print()
        console.print("  [bold]Review before submitting[/bold] (edit in the browser if you like):")
        for question, label in self.state.review:
            echo("review", question, note=label)
        self.state.review.clear()

    # ----- manual completion, capture, and advance --------------------------

    def record_snapshot(self, fields: list[dict[str, Any]]) -> None:
        for index, item in enumerate(snapshot_entries(self.page, fields)):
            question = str(item["question"])
            value = str(item["answer_text"])
            seed, seed_source = self.state.seeds.get(question, ("", "empty"))
            if question in self.state.tier_answers and value == self.state.tier_answers[question]:
                source = self.state.tier_source[question]
            elif seed_source in {"prose", "model"} and value == seed.strip():
                source = seed_source
            else:
                source = "manual"
            fingerprint = str(item.get("field_fingerprint") or f"control-{index}")
            control_key = f"{self.state.step}:{fingerprint}"
            previous = self.state.collected.get(control_key)
            if not value:
                if previous is not None:
                    self.state.collected.pop(control_key)
                    event = {**previous, "event_index": len(self.state.observations),
                             "page_url": self.page.url, "answer_text": "", "source": "manual"}
                    self.state.observations.append(event)
                continue
            proposed_value = (
                self.state.tier_answers.get(question)
                or self.state.planned.get(question)
                or seed
                or None
            )
            if source in {"pattern", "model"} and question in self.state.tier_resolver:
                resolver_id: str | None = self.state.tier_resolver[question]
            elif source in {"prose", "model"}:
                resolver_id = self.state.seed_resolver.get(question) or "model:long_form_v1"
            else:
                resolver_id = previous.get("resolver_id") if previous else None
                if previous:
                    proposed_value = previous.get("proposed_value")
            resolution = self.state.resolutions.get(control_key)
            if resolution is not None:
                source = resolution.source if value == resolution.value else "manual"
                resolver_id = resolution.resolver_id
                proposed_value = resolution.value
            self.state.collected[control_key] = {
                "question": question,
                "answer_text": value,
                "source": source,
                "form": item.get("form") or "short",
                "step_number": self.state.step,
                "page_url": self.page.url,
                "control_kind": str(item["control_kind"]),
                "field_fingerprint": fingerprint,
                "resolver_id": resolver_id,
                "proposed_value": proposed_value,
            }
            if previous is None or previous["answer_text"] != value:
                self.state.observations.append(
                    {
                        "event_index": len(self.state.observations),
                        "step_number": self.state.step,
                        "page_url": self.page.url,
                        "question": question,
                        "answer_text": value,
                        "source": source,
                        "form": item.get("form") or "short",
                        "control_kind": str(item["control_kind"]),
                        "field_fingerprint": fingerprint,
                        "resolver_id": resolver_id,
                        "proposed_value": proposed_value,
                    }
                )
            # Echo what the user typed or chose so they can see it was picked up.
            if source == "manual" and self.state.announced.get(control_key) != value:
                self.state.announced[control_key] = value
                echo("picked up", question, value)

    def wait_and_advance(self, fields: list[dict[str, Any]]) -> ApplicationPageState:
        """Collect manual answers while the user owns page navigation and submission."""
        prior = page_signature(self.page, fields)
        submit_reported = False
        transition_since: float | None = None
        overview_active = False
        while True:
            self.page = self.ctx.browser.active_page(self.page)
            current = inspect_fields(self.page)
            page_state = classify_application_page(self.page, current, self.state.navigation)
            if page_state == ApplicationPageState.SUBMITTED:
                return page_state
            if page_state == ApplicationPageState.OVERVIEW:
                overview_active = True
                transition_since = None
                self.warn_once(
                    "overview-standby",
                    "Ashby Overview is open; standing by until you return to Application.",
                )
                self.sleep()
                continue
            current_signature = page_signature(self.page, current)
            if page_state == ApplicationPageState.FORM and overview_active:
                # Returning from the saved Overview tab resumes this same step.
                prior = current_signature
                overview_active = False
                transition_since = None
            elif page_state == ApplicationPageState.FORM and current_signature == prior:
                transition_since = None
            else:
                transition_since = transition_since or time.monotonic()
                if time.monotonic() - transition_since >= 5:
                    if page_state == ApplicationPageState.FORM:
                        return page_state
                    # The foreground tab is not the form: the user switched
                    # tabs or the page is mid-navigation. Only the user ends a
                    # session, so stand by until a form is in front again.
                    self.warn_once(
                        "form-standby",
                        "The application form is not in the foreground; standing by "
                        "until it is back.",
                    )
                self.sleep()
                continue
            self.record_snapshot(current)
            if has_submit_control(self.page) and not submit_reported:
                console.print()
                console.print("  [bold]Submit found[/bold] — press it in the browser when ready.")
                submit_reported = True
            self.sleep()

    def capture_final_snapshot(self) -> None:
        """Best-effort final read before a clean terminal outcome."""
        try:
            fields = inspect_fields(self.page)
            if fields:
                self.record_snapshot(fields)
        except (Error, AttributeError):
            return

    def final_url(self) -> str:
        try:
            return self.page.url
        except Error:
            return ""

    # ----- driver ----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        console.print(
            "[dim]Ctrl+S records answers for review · Ctrl+A marks submitted and ends · "
            "Ctrl+D skips this listing · Ctrl+C stops and records[/dim]"
        )
        with self.ctx.keys:
            return self._run()

    def _run(self) -> dict[str, Any]:
        submitted = False
        try:
            while True:
                with self.timings.measure("form_wait", step=self.state.step):
                    fields = self.wait_for_form()
                if fields is None:
                    console.print("The page reports the application was submitted.")
                    submitted = True
                    break
                self.emit_warnings(fields)
                with self.timings.measure("deterministic", step=self.state.step):
                    result = self.fill_deterministic(fields)
                if self.ctx.dry_run:
                    return {"final_url": self.page.url, "answers": [], "dry_run": True}
                with self.timings.measure("revealed", step=self.state.step):
                    self.fill_revealed(fields, result)
                if self.ctx.use_claude:
                    with self.timings.measure("short_drafts", step=self.state.step):
                        self.fill_with_claude(fields, result)
                with self.timings.measure("long_form", step=self.state.step):
                    self.fill_long_form(fields)
                with self.timings.measure("cover_letter_review", step=self.state.step):
                    self.fill_cover_letter(fields)
                with self.timings.measure("audit", step=self.state.step):
                    self.audit_and_repair(fields, result)
                self.report_review()
                with self.timings.measure("user_review", step=self.state.step):
                    transition = self.wait_and_advance(fields)
                if transition == ApplicationPageState.SUBMITTED:
                    console.print("The page reports the application was submitted.")
                    submitted = True
                    break
                self.state.step += 1
        except BrowserClosedError:
            console.print("The browser or application tab was closed. Ending autofill.")
        except SubmittedByUser:
            console.print("Marked as submitted.")
            final_url = self.final_url()
            self.capture_final_snapshot()
            self.save_now()
            return {
                "final_url": final_url,
                "answers": list(self.state.collected.values()),
                "submitted": True,
            }
        except SkippedByUser:
            console.print("Skipped. The listing leaves the board; 'os hunt skipped' lists it.")
            final_url = self.final_url()
            self.capture_final_snapshot()
            self.save_now()
            return {
                "final_url": final_url,
                "answers": list(self.state.collected.values()),
                "skipped": True,
            }
        except KeyboardInterrupt:
            final_url = self.final_url()
            self.capture_final_snapshot()
            self.save_now()
            console.print("Stopped. Saved the current form state.")
            return {
                "final_url": final_url,
                "answers": list(self.state.collected.values()),
                "interrupted": True,
            }
        except Error as error:
            if not is_browser_closed_error(self.page, error):
                raise
            console.print("The browser or application tab was closed. Ending autofill.")
        final_url = self.final_url()
        self.capture_final_snapshot()
        self.save_now()
        return {
            "final_url": final_url,
            "answers": list(self.state.collected.values()),
            "submitted": submitted,
        }

    def answers_so_far(self) -> list[dict[str, Any]]:
        return list(self.state.collected.values())
