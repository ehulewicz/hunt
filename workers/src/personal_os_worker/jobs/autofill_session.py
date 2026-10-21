"""The interactive part of `os hunt apply`, without any terminal prompts.

The terminal only reports. A field fills from a fact, through an active
pattern: one you accepted, or one a model verdict taught and the worker checked
(design §8, R67). The model never writes a value; prose and the cover letter's
slots are the only text it drafts.
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
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import Error, Page
from rich.markup import escape

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.tables import console
from personal_os_worker.jobs.account import (
    GOOGLE_HOST,
    AccountFlow,
    AccountOutcome,
    AccountResult,
)
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
    BLANK_KIND,
    MULTI,
    NOT_APPLICABLE,
    AnswerResolution,
    FormFill,
    answer_above,
    build_autofill_plan,
    follow_up_condition,
    is_cover_letter_field,
    is_follow_up_box,
    is_long_form_field,
    is_resume_field,
    is_sensitive_field,
    offered_options,
)
from personal_os_worker.jobs.autofill_timing import AutofillTimings
from personal_os_worker.jobs.browser import (
    CONTROL_TIMEOUT_MS,
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
    upload_into,
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
from personal_os_worker.jobs.explain import Explainer
from personal_os_worker.jobs.formatters import BLANK, polarity
from personal_os_worker.jobs.gateway import (
    click_gateway,
    form_appeared,
    inspect_gateways,
    observed_choice,
)
from personal_os_worker.jobs.keys import KeyWatcher, NoKeys
from personal_os_worker.jobs.learn import Lesson, binding_refusal, lesson_for
from personal_os_worker.jobs.model_errors import ModelError
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
    normalize_navigation_url,
)
from personal_os_worker.jobs.provenance import spans
from personal_os_worker.jobs.registry.events import (
    fill_event,
    planned_events,
    unresolved_events,
)
from personal_os_worker.jobs.registry.kinds import question_of
from personal_os_worker.jobs.remote import FACT_KEY, LinkedKeys, RunLink, gap_items
from personal_os_worker.jobs.repair.repairer import find_field, repair_format, repair_strategy
from personal_os_worker.jobs.replay import Replay, Sighting, replay_pattern, sightings_of
from personal_os_worker.jobs.resolve import Knowledge, Miss, Resolver
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
    # Signs in past an ATS account wall; None without a contact.email fact.
    account: AccountFlow | None = None
    poll_seconds: float = 1.0
    keys: KeyWatcher | NoKeys | LinkedKeys = field(default_factory=KeyWatcher)
    prompt: Callable[[str], str] = input
    # This device's agent, when one is listening: the Hunt page sees and drives the run.
    link: RunLink | None = None


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
    tier_fact: dict[str, str] = field(default_factory=dict)  # question -> fact it came from
    seed_resolver: dict[str, str] = field(default_factory=dict)  # question -> prose/model id
    asked_claude: dict[str, int] = field(default_factory=dict)  # question -> times asked
    judged: set[str] = field(default_factory=set)  # answers already checked against the facts
    check_findings: dict[str, Finding] = field(default_factory=dict)
    model_error: str | None = None
    repair_attempted: set[str] = field(default_factory=set)  # questions repair already retried
    budget: Budget = field(default_factory=Budget)
    misses: dict[str, Miss] = field(default_factory=dict)  # answer key -> why unanswered
    needed: dict[str, str] = field(default_factory=dict)  # question -> fact the model named
    # Questions a none fact leaves blank on purpose: question -> that fact.
    blanks: dict[str, str] = field(default_factory=dict)
    # A choice you made that a fact contradicts: control key -> the value, never saved.
    withheld: dict[str, str] = field(default_factory=dict)
    na_flagged: set[str] = field(default_factory=set)  # N/A follow-ups that now apply
    outlined: set[str] = field(default_factory=set)  # questions the audit left for you
    gates_tried: set[str] = field(default_factory=set)  # gate questions already answered once
    gate_pending: dict[str, Any] | None = None  # a gate left for you, watched for your click
    account_tried: set[str] = field(default_factory=set)  # account page URLs already tried
    account_parked: bool = False  # the account flow gave the wall to you; it stays yours
    step: int = 0
    form_seen: bool = False
    navigation: TabNavigation | None = None
    # The accepted answers a learned change is replayed against, read once a run;
    # None when they could not be read.
    sightings: list[Sighting] | None = None
    sightings_read: bool = False
    # (question, fact) -> why a lesson waits in the Inbox: it is saved once a run.
    held: dict[tuple[str, str], str] = field(default_factory=dict)
    # question -> (the model's draft, the previous answers it started from), for
    # each long-form answer's provenance.
    origins: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    similar_prose: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


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
    "learned": "magenta",
    "source": "dim",
    "consent": "dim",
    "long-form": "cyan",
    "prose": "cyan",
    "letter": "cyan",
    "picked up": "cyan",
    "saved": "green",
    "fact": "green",
    "kept": "dim",
    "blank": "dim",
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
    return question_of(field)



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
    all text inputs, required or optional (no options).
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
        else:
            questions[label] = ShortQuestion(label, ())
    for group, members in groups.items():
        if any(member.get("filled") is True for member in members):
            continue
        options = tuple(
            str(member.get("label") or member.get("value") or "") for member in members
        )
        questions[group] = ShortQuestion(group, options)
    return list(questions.values())


def derived_refusal(key: str) -> str | None:
    """Why a fact is not asked for: the career record or the posting decides it.

    Storing it would freeze it, and a referrer named here would follow every
    application after.
    """
    from personal_os_worker.jobs.facts.catalog import entry

    known = entry(key)
    if known is None or known.source == "stored":
        return None
    hint = " (apply --referred NAME)" if key.startswith("referral.") else ""
    return f"{key} is derived, not asked for{hint}"


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

    def call_model[T](self, operation: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:
        """Keep model outages visible without ending the live application."""
        if self.state.model_error is not None:
            return None
        try:
            return operation(*args, **kwargs)
        except (ModelError, subprocess.TimeoutExpired, OSError) as error:
            self.state.model_error = str(error) if isinstance(error, ModelError) else (
                "Claude timed out or could not start; drafting and answer checks unavailable"
            )
            echo("failed", "Drafting and answer audit", note=self.state.model_error)
            return None

    # ----- reporting -------------------------------------------------------

    def persist_step(self, run_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Attach the exact inputs used for each attempt before storing its observation."""
        payload = dict(payload)
        if "fill_events" in payload:
            knowledge = self.ctx.knowledge
            snapshot = {"facts": knowledge.facts, "types": knowledge.types, "seed": knowledge.seed}
            payload["fill_events"] = [
                event | {"knowledge": snapshot} for event in payload["fill_events"]
            ]
        return self.ctx.api.persist_autofill_step(run_id, payload)

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
        self.persist_step(
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
        if self.ctx.link is not None:
            self.ctx.link.tab(self.page)
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

    def _save_now(self, *, quiet: bool = False) -> int:
        """Persist newly observed values for this run without approving reuse."""
        pending = self.state.observations[self.state.persisted_observations :]
        for offset in range(0, len(pending), 500):
            batch = pending[offset : offset + 500]
            self.persist_step(
                self.ctx.run_id,
                {
                    "step_number": self.state.step,
                    "url": self.page.url,
                    "observations": batch,
                },
            )
            self.state.persisted_observations += len(batch)
        if not quiet:
            console.print(
                f"  [dim]recorded {len(pending)} answer observation(s) for review[/dim]"
            )
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
            if page_state == ApplicationPageState.ACCOUNT:
                # The account step is the gate in front of the gate.
                result = self.through_the_account_wall()
                if result is not None and result.outcome == AccountOutcome.SIGNED_IN:
                    continue
                wall_reported = wall_reported or result is not None
            elif self.pass_the_gate():
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

    # ----- the account wall --------------------------------------------------

    def through_the_account_wall(self) -> AccountResult | None:
        """Sign in past an account page, once per page; None when it was not tried.

        A wall the flow could not pass is reported once and then left to you:
        the flow never runs again in this session, so it cannot type over what
        you are doing in the browser.
        """
        key = normalize_navigation_url(self.page.url)
        if self.ctx.dry_run or self.state.account_parked or key in self.state.account_tried:
            return None
        # Google's own sign-in (a popup left open, a late redirect) is never an ATS wall.
        if urlsplit(self.page.url).hostname == GOOGLE_HOST:
            return None
        self.state.account_tried.add(key)
        if self.ctx.account is None:
            console.print(
                "  [dim]account page: set the contact.email fact and I sign in for you; "
                "for now it is yours[/dim]"
            )
            return None
        console.print(f"  account page: signing in as {self.ctx.account.email}")
        result = self.ctx.account.run(self.page)
        if result.outcome == AccountOutcome.SIGNED_IN:
            console.print(f"  [green]{result.reason}[/green]")
            return result
        self.state.account_parked = True
        console.print(
            f"[yellow]Account wall:[/yellow] {result.reason}. "
            "Finish it in the open browser; I am watching the page."
        )
        self.report_page_failure("account_wall", result.reason)
        return result

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
        resolver = Resolver(self.ctx.knowledge)
        resolution = resolver.resolve(gate)
        # A gate a pattern decides is never the model's, even when its fact is missing.
        if resolution is None and self.ctx.use_claude and resolver.decision(question) is None:
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
        """One bounded pass: which fact says how you start an application.

        The model only names the fact. What it teaches is checked and saved as a
        pattern, and the gate is answered through the resolver like any choice.
        A dry run writes nothing, so it learns nothing and asks nothing.
        """
        if self.ctx.dry_run:
            return None
        question = str(gate["label"])
        console.print("  [dim]asking Claude which fact says how to start this application[/dim]")
        verdicts = self.call_model(self.ctx.short_drafter,
            [ShortQuestion(question, tuple(str(item) for item in gate["options"]))],
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
        ) or {}
        for verdict in verdicts.values():
            if verdict.kind == "no_fact" and verdict.missing_fact:
                self.state.needed[verdict.question] = verdict.missing_fact
        if not self.learn(verdicts, {question: gate}):
            return None
        return Resolver(self.ctx.knowledge).resolve(gate)

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
        self.persist_step(
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
                        options=[str(option) for option in gate.get("options") or []],
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
        self,
        fields: list[dict[str, Any]],
        *,
        only: set[str] | None = None,
        note: str = "appeared after answering",
    ) -> dict[str, Any]:
        """One deterministic pass; `only` limits it to these question keys, `note` says why."""
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
            # Prose controls are seeded by fill_long_form, which knows their source,
            # unless a pattern names the fact a big box is asking for ("the school
            # you will attend", in a textarea): a fact beats a draft. So does the
            # N/A of a follow-up the answer above rules out.
            def from_a_fact(fill: FormFill) -> bool:
                return fill.resolution is not None and (
                    fill.resolution.fact_key is not None or fill.resolution == NOT_APPLICABLE
                )

            plan = type(plan)(
                tuple(
                    fill for fill in plan.fills
                    if not is_long_form_field(fields[fill.index]) or from_a_fact(fill)
                ),
                plan.unanswered_required_questions,
                plan.reasons | {
                    fill.index: "long-form: seeded from approved prose, else a Claude draft"
                    for fill in plan.fills
                    if is_long_form_field(fields[fill.index]) and not from_a_fact(fill)
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
            console.print(f"  [dim]{len(only)} question(s) {note}; "
                          f"{len(plan.fills)} planned[/dim]")
        explainer = self.explainer()
        if explainer is not None:
            explainer.begin(
                f"step {self.state.step + 1}, "
                + ("patterns and engine" if only is None else f"questions that {note}"),
                fields,
                plan,
                resolver,
                scope=in_scope,
            )
        if self.ctx.dry_run:
            if explainer is not None:
                explainer.dry_run()
            self.persist_step(
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
            if fill.kind == BLANK_KIND and question and fill.resolution is not None:
                fact = str(fill.resolution.fact_key or "")
                self.state.blanks[question] = fact
                echo("blank", question, note=f"{fact} is none")
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
                    if fill.resolution.fact_key:
                        self.state.tier_fact[question] = fill.resolution.fact_key
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
        issues = self.persist_step(
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

    # ----- learning from the model ---------------------------------------------

    def failure(self, question: str) -> dict[str, Any]:
        """The failure a change made in this run answers: the run, and its question."""
        key = normalize_answer_question(question)
        return {"run_id": self.ctx.run_id} | (
            {"question_key": key} if key and len(key) <= 1000 else {}
        )

    def bind(self, question: str, fact_key: str, *, origin: str) -> str | None:
        """Bind one question to a fact and add it to what this run knows; why not, if not."""
        return self.remember(
            {"kind": "binding", "question": question}, fact_key, origin=origin,
            failure=self.failure(question),
        )

    def remember(
        self,
        shape: dict[str, Any],
        fact_key: str,
        *,
        origin: str,
        status: str = "active",
        failure: dict[str, Any] | None = None,
        verification: dict[str, int] | None = None,
        use_for_run: bool = False,
    ) -> str | None:
        """Save a pattern, and add it to this run's knowledge once it is active; why not,
        if not. A binding replaces its question's binding here as it does in the API."""
        try:
            created = self.ctx.api.create_pattern(
                **shape, fact_key=fact_key, origin=origin, status=status, failure=failure,
                verification=verification,
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 409:
                # It would undo a decision of yours, or it already waits in the Inbox.
                return str(error.response.json().get("detail") or "the API refused it")
            return f"the pattern could not be saved (HTTP {error.response.status_code})"
        except (httpx.HTTPError, ReferenceError, RuntimeError) as error:
            return f"the pattern could not be saved ({error.__class__.__name__})"
        if created.get("status") == "active" or use_for_run:
            self.ctx.knowledge = self.ctx.knowledge.with_pattern(created | {"status": "active"})
        return None

    def sightings(self) -> list[Sighting] | None:
        """The answers you accepted, read once a run; None when they cannot be read."""
        if not self.state.sightings_read:
            self.state.sightings_read = True
            try:
                self.state.sightings = sightings_of(self.ctx.api.sightings())
            except (httpx.HTTPError, ReferenceError, RuntimeError):
                self.state.sightings = None
        return self.state.sightings

    def checked(self, lesson: Lesson) -> tuple[Lesson, Replay | None]:
        """Replay a lesson against the answers you accepted (R69).

        An expression that changes one for the worse falls back, once, to
        binding the question alone, replayed in turn. None when the answers
        could not be read: the lesson is saved as M2 saved it, unreplayed.
        """
        sightings = self.sightings()
        if sightings is None:
            return lesson, None
        replay = replay_pattern(sightings, self.ctx.knowledge, lesson.pattern())
        if replay.passed or lesson.kind == "binding" or binding_refusal(
            lesson.question, patterns=list(self.ctx.knowledge.patterns)
        ) is not None:
            return lesson, replay
        bound = lesson.bound(f"expression not kept: {replay.summary()}")
        return bound, replay_pattern(sightings, self.ctx.knowledge, bound.pattern())

    def save_lesson(
        self,
        question: str,
        fact_key: str,
        expression: str | None,
        questions: list[str] | None,
        target: dict[str, Any],
        *,
        overriding: bool = False,
    ) -> str | None:
        """Check what a verdict teaches and save it active; why nothing was saved, if not.

        The fact must be one this control can take (`target` is the field with
        its label and options), or the lesson would only ever fail at format.
        `questions` are the labels already met, which an expression must leave
        answered as they are. Without them no expression can be checked, so
        only the question itself is bound. `overriding` is for a question a
        pattern answered wrongly.
        """
        if Resolver(self.ctx.knowledge).render_fact(fact_key, target, question) is None:
            return f"{fact_key} fits none of this control's options"
        lesson, refusal = lesson_for(
            question,
            fact_key,
            expression if questions is not None else None,
            patterns=list(self.ctx.knowledge.patterns),
            questions=questions or [],
            overriding=overriding,
        )
        if lesson is None:
            return refusal
        if (question, fact_key) in self.state.held:
            return self.state.held[(question, fact_key)]
        lesson, replay = self.checked(lesson)
        held = replay is not None and not replay.passed
        failed = self.remember(
            lesson.shape(),
            fact_key,
            origin="model",
            status="candidate" if held else "active",
            failure=self.failure(question),
            verification=replay.verification() if replay is not None else None,
            use_for_run=held and overriding,
        )
        if lesson.kind == "expression" and failed == "this expression already waits in the Inbox":
            lesson, replay = self.checked(lesson.bound(f"expression not kept: {failed}"))
            held = replay is not None and not replay.passed
            failed = self.remember(
                lesson.shape(), fact_key, origin="model",
                status="candidate" if held else "active", failure=self.failure(question),
                verification=replay.verification() if replay is not None else None,
                use_for_run=held and overriding,
            )
        if failed is not None:
            return failed
        if held and replay is not None:
            reason = f"it waits in the Inbox as a pattern check: {replay.summary()}"
            if overriding:
                # The current answer was independently checked against a fact.
                # Repair this run, while the conflicting historical answer keeps
                # the reusable lesson pending the user's review.
                echo("review", question, note=f"correcting this run only; {reason}")
                return None
            self.state.held[(question, fact_key)] = reason
            return reason
        what = f"/{lesson.expression}/" if lesson.kind == "expression" else "bound"
        echo("learned", question, note=f"{what} -> {fact_key}"
             + (f"; {lesson.note}" if lesson.note else "")
             + (f"; {replay.summary()}" if replay is not None else ""))
        return None

    def learn(
        self, verdicts: dict[str, Verdict], targets: dict[str, dict[str, Any]]
    ) -> set[str]:
        """Save a checked pattern for every question a verdict says a fact answers.

        `targets` holds each asked question's field, labelled with the question
        and carrying the options it was asked with. Returns the questions a
        saved pattern now decides, for the pattern pass to fill. A dry run
        writes nothing, so it learns nothing.
        """
        fits = [
            verdict for verdict in verdicts.values()
            if verdict.kind == "fits_fact" and verdict.fact_key and verdict.question in targets
        ]
        if not fits or self.ctx.dry_run:
            return set()
        questions: list[str] | None
        try:
            questions = [str(item.get("sample_label") or "") for item in self.ctx.api.questions()]
        except (httpx.HTTPError, ReferenceError, RuntimeError):
            questions = None
        learned: set[str] = set()
        for verdict in fits:
            refusal = self.save_lesson(
                verdict.question, str(verdict.fact_key), verdict.expression, questions,
                targets[verdict.question],
            )
            if refusal is None:
                learned.add(verdict.question)
            else:
                echo("left", verdict.question, note=f"{verdict.fact_key}: {refusal}")
        return learned

    def refill_learned(
        self, fields: list[dict[str, Any]], learned: set[str], result: dict[str, Any]
    ) -> bool:
        """The pattern pass over the questions a lesson now decides; True when one filled."""
        if not learned or self.form_is_gone():
            return False
        more = self.fill_deterministic(
            fields,
            only={normalize_answer_question(question) for question in learned},
            note="answered by what was just learned",
        )
        for name in ("filled_fields", "skipped_fields", "failures"):
            result.setdefault(name, []).extend(more.get(name) or [])
        return bool(more.get("filled_fields"))

    def fill_with_claude(self, fields: list[dict[str, Any]], result: dict[str, Any]) -> None:
        """One bounded pass asking Claude which fact answers each question nothing decides.

        Claude only names a fact; what it teaches is saved as a pattern, and the
        pattern pass fills the field. A question an active pattern decides is
        never put to it, even when its fact is missing or fits no option.
        """
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
        decides = Resolver(self.ctx.knowledge).decision
        questions = [
            question
            for question in _claude_questions(current)
            if self.may_ask(question.question)
            and question.question not in self.state.blanks
            and decides(question.question) is None
        ]
        if not questions:
            return
        for pending_question in questions:
            self.note_asked(pending_question.question)
        console.print(f"  [dim]asking Claude which fact answers {len(questions)} question(s)[/dim]")
        verdicts = self.call_model(self.ctx.short_drafter,
            questions,
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
        ) or {}
        for verdict in verdicts.values():
            if verdict.kind == "no_fact" and verdict.missing_fact:
                self.state.needed[verdict.question] = verdict.missing_fact
        if not any(item.kind == "fits_fact" for item in verdicts.values()):
            console.print("  [dim]Claude found no fact that answers them[/dim]")
            return
        if self.form_is_gone():
            return
        targets = {
            question.question: dict(field)
            | {"label": question.question, "options": list(question.options)}
            for question in questions
            if (field := find_field(current, question.question)) is not None
        }
        self.refill_learned(fields, self.learn(verdicts, targets), result)

    # ----- long-form drafts ------------------------------------------------

    def references(self, question: str) -> list[dict[str, Any]]:
        """Your approved writing on similar questions; the archive is never a suggestion."""
        key = normalize_answer_question(question)
        if key in self.state.similar_prose:
            return self.state.similar_prose[key]
        search = getattr(self.ctx.api, "similar_questions", None)
        if search is not None:
            try:
                matches = search(question, prose_only=True)
                references = [
                    {"question": match["question"], "answer_text": prose["text"]}
                    for match in matches for prose in match.get("prose") or []
                    if prose.get("approved") and str(prose.get("text") or "").strip()
                ][:5]
                self.state.similar_prose[key] = references
                return references
            except (httpx.HTTPError, RuntimeError):
                self.warn_once(
                    "similarity-unavailable", "Question similarity is unavailable; "
                    "using wording matches for approved prose.",
                )
        writing = [
            {"question": item.get("question_label"), "answer_text": item.get("text")}
            for item in self.ctx.knowledge.prose
        ]
        self.state.similar_prose[key] = similar_answers(question, writing)
        return self.state.similar_prose[key]

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
            text = str(approved["text"]).strip()
            self.state.origins[question] = ("", (text,))
            return text, "prose", f"prose:{approved.get('id')}", "approved prose (edit freely)"
        if not self.ctx.use_claude:
            return "", "empty", "", "drafting disabled; write it, it is saved at the run's end"
        references = self.references(question)
        if not self.may_ask(question):
            return "", "empty", "", "no usable draft after two attempts; answer still needed"
        self.note_asked(question)
        draft = self.call_model(self.ctx.drafter,
            question=question,
            base=None,
            posting={
                "company": self.ctx.company,
                "role": self.ctx.title,
                "description": self.ctx.description,
            },
            resume=self.ctx.resume_json,
            facts=self.ctx.knowledge.values(),
            references=references,
            limit=limit,
            attempts=1,
        )
        if draft:
            self.state.origins[question] = (
                draft, tuple(str(item.get("answer_text") or "") for item in references)
            )
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
            if question in self.state.tier_answers or question in self.state.blanks:
                continue  # a pattern answered it from a fact this step
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
            slots = self.call_model(
                self.ctx.letter_drafter, template, posting=posting, resume=self.ctx.resume_json
            )
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
                    upload_into(self.page, fresh_control(self.page, item), pdf)
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
        return offered_options(fields, field)

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
                or (control.get("type") == "radio" and not control.get("group"))
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
        answers = self.filled_answers(fields)
        live = {answer.question: answer.answer for answer in answers}
        self.state.check_findings = {
            question: finding for question, finding in self.state.check_findings.items()
            if live.get(question) == finding.answer
        }
        fresh = [
            answer
            for answer in answers
            if answer.question not in self.state.judged
        ]
        if not fresh:
            return list(self.state.check_findings.values())
        self.state.judged.update(answer.question for answer in fresh)
        console.print(f"  [dim]checking {len(fresh)} answer(s) against the facts[/dim]")
        judgements = self.call_model(self.ctx.checker,
            fresh,
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
        ) or {}
        held = {answer.question: answer for answer in fresh}
        required = {
            _question_for(item) for item in fields if item.get("required") is True
        }
        for unchecked in fresh:
            if unchecked.question not in judgements:
                self.state.check_findings[unchecked.question] = Finding(
                    unchecked.question, "model_error", unchecked.question in required,
                    self.state.model_error or "the answer check returned no judgement",
                    answer=unchecked.answer, source=unchecked.source,
                )
        for judgement in judgements.values():
            answer = held.get(judgement.question)
            if judgement.kind != "wrong" or not judgement.fact_key or answer is None:
                continue
            self.state.check_findings[judgement.question] = Finding(
                judgement.question,
                "wrong_answer",
                required=judgement.question in required,
                note=judgement.note or f"{judgement.fact_key} says otherwise",
                fact_key=judgement.fact_key,
                answer=answer.answer,
                source=answer.source,
            )
        return list(self.state.check_findings.values())

    def pattern_pass(self, fields: list[dict[str, Any]], failures: list[dict[str, Any]]) -> bool:
        """Refill every open question a pattern or the engine answers, before any model call.

        A value a pattern filled that the page then lost (a re-render, a cleared
        widget) comes back from the same pattern here, never from a model. True
        when anything was filled.
        """
        try:
            current = inspect_fields(self.page)
            open_questions = audit_filled_form(current)
        except Error:
            return False
        wanted = {
            normalize_answer_question(question)
            for question in open_questions["required_unfilled"]
            + open_questions["optional_unfilled"]
            if question not in self.state.blanks
        }
        plan = build_autofill_plan(current, resolver=Resolver(self.ctx.knowledge))
        # Only what a pattern answers: an upload or a consent click the engine
        # made is never replayed, since some sites append a second attachment.
        planned = {
            key
            for fill in plan.fills
            if fill.kind == "text" and fill.chooser is None
            and fill.resolution is not None and fill.resolution.fact_key is not None
            and (key := normalize_answer_question(_question_for(current[fill.index]))) in wanted
        }
        if not planned:
            return False
        more = self.fill_deterministic(fields, only=planned, note="a pattern answers again")
        failures.extend(more.get("failures") or [])
        return bool(more.get("filled_fields"))

    def audit(
        self, fields: list[dict[str, Any]], failures: list[dict[str, Any]]
    ) -> list[Finding]:
        """What is still wrong with this page: nothing answered it, or a fact disagrees.

        A question a pattern answers but that is still empty is a control error:
        the value was right and did not stay, which is the repair lane's, never
        a model's.
        """
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
        resolver = Resolver(self.ctx.knowledge)
        resolved: dict[str, AnswerResolution] = {}
        for question in open_questions["required_unfilled"] + open_questions["optional_unfilled"]:
            field = find_field(current, question)
            # A signature or a password is yours: no lane may type into it.
            if field is None or question in self.state.blanks or is_sensitive_field(field):
                continue
            resolution = resolver.resolve(
                dict(field) | {"label": question, "options": self.options_for(current, field)}
            )
            if resolution is not None and resolution.formatter != BLANK:
                resolved[normalize_answer_question(question)] = resolution
        misses = {**self.state.misses, **resolver.misses}
        findings = [
            classify_empty(
                question,
                required=required,
                needed=self.state.needed,
                misses=misses,
                failures=by_question,
                resolved=resolved,
            )
            for question, required in (
                [(item, True) for item in open_questions["required_unfilled"]]
                + [(item, False) for item in open_questions["optional_unfilled"]]
            )
            if question not in self.state.blanks
        ]
        # None remains a known fact even when this control cannot accept N/A.
        findings += [
            Finding(
                question,
                "fact_gap",
                question in open_questions["required_unfilled"],
                f"{fact} is none, but this control cannot represent N/A; answer still needed",
                fact_key=fact,
            )
            for question in (
                open_questions["required_unfilled"] + open_questions["optional_unfilled"]
            )
            if (fact := self.state.blanks.get(question)) is not None
        ]
        unidentified = {
            _question_for(item): item for item in current
            if item.get("type") == "radio" and not item.get("group")
        }
        findings = [item for item in findings if item.question not in unidentified]
        findings += [Finding(question, "detection_error", item.get("required") is True,
                             "radio options have no question label; extraction needs repair")
                     for question, item in unidentified.items()]
        findings = [
            replace(item, cause="draft_error", note=self.state.model_error
                    or "prose still needs an answer from approved writing or a grounded draft")
            if item.cause == "pattern_gap"
            and (control := find_field(current, item.question)) is not None
            and is_long_form_field(control) else item
            for item in findings
        ]
        return order(findings + self.wrong_answers(current))

    def audit_and_repair(
        self, fields: list[dict[str, Any]], result: dict[str, Any]
    ) -> None:
        """Look, fix what has a path, look again, then report whatever is left.

        The repairs are silent: each one echoes the line it changed, and only
        what no lane could fix is listed for you at the end.
        """
        failures = list(result.get("failures") or [])
        self.reconcile_follow_ups(fields)
        self.pattern_pass(fields, failures)
        findings = self.audit(fields, failures)
        for _round in range(max(1, self.ctx.audit_rounds)):
            if not findings:
                break
            if not self.remediate(fields, findings):
                break
            self.pattern_pass(fields, failures)
            findings = self.audit(fields, failures)
        self.report_open(findings)
        if self.ctx.link is not None:
            self.ctx.link.gaps(gap_items(findings))

    def remediate(self, fields: list[dict[str, Any]], findings: list[Finding]) -> bool:
        """Run each finding's repair path; True when anything on the page changed."""
        acted = False
        prose = {item.question for item in findings if item.cause == "draft_error"}
        if prose:
            current = inspect_fields(self.page)
            self.fill_long_form([item for item in current if _question_for(item) in prose])
            acted = any(item.get("filled") is True and _question_for(item) in prose
                        for item in inspect_fields(self.page))
        if self.ctx.repair:
            acted = self.queue_repairs(fields, findings) or acted
        acted = self.ask_again(fields, findings) or acted
        for finding in findings:
            if finding.cause == "wrong_answer":
                acted = self.correct_answer(fields, finding) or acted
            elif finding.cause == "fact_gap" and self.ctx.ask_facts:
                acted = self.ask_for_fact(fields, finding) or acted
        # A repair above may have changed the answer a follow-up depends on.
        return self.reconcile_follow_ups(fields) or acted

    def reconcile_follow_ups(self, fields: list[dict[str, Any]]) -> bool:
        """Make every "If yes…" follow-up agree with the answer just above it, as the page holds it.

        An N/A the worker wrote is cleared once the answer above no longer rules
        the follow-up out (it was answered the other way, or the fill above never
        landed), so the follow-up is answered from a fact or left for you. An
        empty follow-up the answer above rules out gets N/A. An N/A you typed is
        yours. True when anything on the page changed.
        """
        try:
            current = inspect_fields(self.page)
        except Error:
            return False
        acted = False
        for item in current:
            question = _question_for(item)
            condition = follow_up_condition(question)
            if condition is None or not is_follow_up_box(item) or is_sensitive_field(item):
                continue
            above = answer_above(current, item)
            ruled_out = above is not None and above != condition
            try:
                value = str(fresh_control(self.page, item).input_value(
                    timeout=CONTROL_TIMEOUT_MS
                )).strip()
            except (Error, RuntimeError):
                continue
            ours = (
                self.state.tier_resolver.get(question) == NOT_APPLICABLE.resolver_id
                and value == NOT_APPLICABLE.value
            )
            if ours and not ruled_out:
                acted = self.clear_not_applicable(item, above=above) or acted
            elif not value and ruled_out:
                shown = self.apply_value(
                    fields, item, NOT_APPLICABLE.value,
                    source=NOT_APPLICABLE.source, resolver_id=NOT_APPLICABLE.resolver_id,
                )
                if shown is not None:
                    echo("source", question, shown, note="the answer above rules it out")
                    acted = True
        return acted

    def clear_not_applicable(self, field: dict[str, Any], *, above: bool | None) -> bool:
        """Take back an N/A the worker wrote into a follow-up that does apply."""
        question = _question_for(field)
        try:
            control = fresh_control(self.page, field)
            set_registered_value(control, "")
            cleared = not str(control.input_value(timeout=CONTROL_TIMEOUT_MS)).strip()
        except (Error, RuntimeError):
            cleared = False
        if not cleared:
            echo("failed", question, NOT_APPLICABLE.value, note="could not take back N/A")
            return False
        for answers in (self.state.tier_answers, self.state.tier_source,
                        self.state.tier_resolver, self.state.tier_fact):
            answers.pop(question, None)
        why = (
            f"the question above was answered {'Yes' if above else 'No'}, so it applies"
            if above is not None else "nothing above rules it out"
        )
        echo("repaired", question, note=f"{why}: N/A taken back")
        return True

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
        issues = self.persist_step(
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
        """Put every question nothing decides back to the model, once more, with the options.

        A fact gap a pattern names is not the model's: the pattern is right and
        only its fact is missing. What the model teaches is saved as a pattern,
        and the pattern pass fills the field.
        """
        if not self.ctx.use_claude:
            return False
        questions: list[ShortQuestion] = []
        targets: dict[str, dict[str, Any]] = {}
        for finding in findings:
            if finding.cause not in {"pattern_gap", "fact_gap"} or finding.fact_key:
                continue
            field = find_field(fields, finding.question)
            if (
                field is None
                or not self.may_ask(finding.question)
                or finding.question in self.state.blanks
                or is_sensitive_field(field)
            ):
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
        verdicts = self.call_model(self.ctx.short_drafter,
            questions,
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
        ) or {}
        for verdict in verdicts.values():
            if verdict.kind == "no_fact" and verdict.missing_fact:
                self.state.needed[verdict.question] = verdict.missing_fact
        learned = self.learn(verdicts, {
            name: dict(field) | {"label": name, "options": self.options_for(fields, field)}
            for name, field in targets.items()
        })
        return self.refill_learned(fields, learned, {})

    def correct_answer(self, fields: list[dict[str, Any]], finding: Finding) -> bool:
        """Bind a question the worker answered wrongly to the fact that contradicts it, and
        refill it through the pattern path.

        The model only named the fact. An answer you typed, one the engine
        chose, and one a binding you accepted produced are reported instead.
        """
        key = str(finding.fact_key or "")
        if key not in self.ctx.knowledge.facts:
            return False
        engine = self.state.tier_resolver.get(finding.question, "").startswith("engine:")
        if finding.source not in WORKER_SOURCES or engine:
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
        target = dict(field) | {
            "label": finding.question, "options": self.options_for(fields, field)
        }
        rendering = Resolver(self.ctx.knowledge).render_fact(key, target, finding.question)
        if rendering is None or rendering.value == finding.answer:
            return False
        refusal = self.save_lesson(
            finding.question, key, None, None, target, overriding=True
        )
        if refusal is not None:
            echo("review", finding.question, finding.answer,
                 note=f"{key} says otherwise ({finding.note}); {refusal}")
            return False
        resolution = Resolver(self.ctx.knowledge).resolve(target)
        if resolution is None or resolution.value == finding.answer:
            return False
        shown = self.apply_value(
            fields,
            field,
            resolution.value,
            source="pattern",
            resolver_id=resolution.resolver_id,
            fact_key=resolution.fact_key,
            formatter=resolution.formatter,
            pattern_id=resolution.pattern_id,
        )
        if shown is None:
            return False
        echo("repaired", finding.question, shown, note=f"{key} said {finding.note or 'otherwise'}")
        return True

    def ask_for_fact(self, fields: list[dict[str, Any]], finding: Finding) -> bool:
        """Ask at the terminal for the fact this question needs, then fill it in."""
        from personal_os_worker.jobs.facts.catalog import entry
        from personal_os_worker.jobs.facts.commands import VALUE_TYPES

        key = str(finding.fact_key or "")
        console.print()
        console.print(f"  [bold]{escape(finding.question)}[/bold]")
        if not key:
            console.print(f"  needs a fact: {finding.missing_fact}", style="dim")
            key = self.ctx.prompt("  fact key (blank to skip): ").strip()
            if not key:
                return False
        refusal = derived_refusal(key)
        if refusal is not None:
            console.print(f"  {refusal}", style="dim")
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
        ok, message = self.set_fact_and_fill(
            fields, finding.question, key, label=label, value_type=str(value_type), raw=raw
        )
        if not ok:
            console.print(f"  {message}", style="red")
        return ok

    def answer_remote_facts(self, fields: list[dict[str, Any]]) -> None:
        """Facts the Hunt page sent for this step's gaps: set each, then fill its question."""
        link = self.ctx.link
        if link is None:
            return
        for answer in link.fact_answers():
            question = str(answer.get("question") or "")
            key = str(answer.get("key") or "").strip()
            ok, message = (
                self.set_fact_and_fill(
                    fields,
                    question,
                    key,
                    label=str(answer.get("label") or "").strip(),
                    value_type=str(answer.get("value_type") or "").strip(),
                    raw=str(answer.get("value") or ""),
                )
                if key
                else (False, "name the fact this question needs")
            )
            if not ok:
                echo("left", question, note=message)
            link.fact_result(question, ok=ok, message=message)

    def set_fact_and_fill(
        self,
        fields: list[dict[str, Any]],
        question: str,
        key: str,
        *,
        label: str,
        value_type: str,
        raw: str,
    ) -> tuple[bool, str]:
        """Store the fact a question needs, then fill the question from it.

        The terminal's --ask-facts and the Hunt page both end here; they differ
        only in where the key, label, type, and value come from. True once the
        fact is stored, with what was filled; False with the reason it was not.
        """
        from personal_os_worker.jobs.facts.catalog import entry
        from personal_os_worker.jobs.facts.commands import VALUE_TYPES, parse_value

        if not FACT_KEY.fullmatch(key):
            return False, f"{key!r} is not a fact key; use dotted lowercase words"
        refusal = derived_refusal(key)
        if refusal is not None:
            return False, refusal
        known = entry(key)
        label = known.label if known else label
        value_type = known.value_type if known else value_type
        if not label or value_type not in VALUE_TYPES:
            return False, "a fact needs a label and a type"
        try:
            value = parse_value(value_type, raw)
        except ValueError as error:
            return False, str(error)
        try:
            self.ctx.api.set_fact(
                key, value, label=label, value_type=value_type, failure=self.failure(question)
            )
        except httpx.HTTPStatusError as error:
            return False, f"the API refused {key} (HTTP {error.response.status_code})"
        self.refresh_knowledge()
        echo("fact", question, str(value), note=f"set {key}")
        field = find_field(fields, question)
        if field is None:
            return True, f"set {key}"
        decided = Resolver(self.ctx.knowledge).decision(question)
        if decided is None or str(decided["fact_key"]) != key:
            # You named the fact this question needs: bind it, as the Inbox would.
            refusal = self.bind(question, key, origin="inbox")
            if refusal is not None:
                return True, f"set {key}; {refusal}"
        resolution = Resolver(self.ctx.knowledge).resolve(
            dict(field) | {"label": question, "options": self.options_for(fields, field)}
        )
        if resolution is None:
            return True, f"set {key}"
        shown = self.apply_value(
            fields,
            field,
            resolution.value,
            source="pattern",
            resolver_id=resolution.resolver_id,
            fact_key=resolution.fact_key,
            formatter=resolution.formatter,
            pattern_id=resolution.pattern_id,
        )
        if shown is not None:
            echo("pattern", question, shown, note=key)
        return True, shown or f"set {key}"

    def refresh_knowledge(self) -> None:
        """Rebuild what the run knows after a fact was set, keeping its preferences."""
        from personal_os_worker.jobs.facts.derived import POSTING_FACTS

        old = self.ctx.knowledge
        rebuilt = Knowledge.build(
            facts=self.ctx.api.facts(),
            career=self.ctx.career,
            patterns=old.patterns,
            prose=old.prose,
            company=self.ctx.company,
            seed=old.seed,
        )
        # Posting facts (its term and window, a referral, your history with the
        # company) came from this run's inputs, not from your facts: keep them.
        kept = {item.key for item in POSTING_FACTS if item.key in old.facts}
        self.ctx.knowledge = replace(
            rebuilt,
            facts={**rebuilt.facts, **{key: old.facts[key] for key in kept}},
            types={**rebuilt.types, **{key: old.types[key] for key in kept if key in old.types}},
            preferences=old.preferences,
        )

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
        pattern_id: str | None = None,
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
            shown or value, source, resolver_id, fact_key=fact_key, formatter=formatter,
            pattern_id=pattern_id,
        )
        self.persist_step(
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
                        options=offered_options(fields, field),
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
        if fact_key:
            self.state.tier_fact[question] = fact_key
        else:
            self.state.tier_fact.pop(question, None)
        return shown

    def report_open(self, findings: list[Finding]) -> None:
        """What the audit could not fix, required first, outlined on the page.

        Optional fields count: nothing is left blank unless you leave it. Each
        line says the cause, so what you do about it is never a guess.
        """
        self.state.outlined = {item.question for item in findings}
        with contextlib.suppress(Error):
            highlight_unfilled(self.page, inspect_fields(self.page), self.state.outlined)
        if not findings:
            console.print("  [dim]every field is filled, and no fact disagrees[/dim]")
            return
        empty = [item for item in findings if item.cause not in {"wrong_answer", "model_error"}]
        wrong = [item for item in findings if item.cause == "wrong_answer"]
        unchecked = [item for item in findings if item.cause == "model_error"]
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
        if unchecked:
            console.print("  [bold]Answer audit incomplete[/bold]:")
            for item in unchecked:
                echo("review", item.question, item.answer, note=item.note)

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

    def contradiction(
        self, fields: list[dict[str, Any]], question: str, value: str
    ) -> tuple[str, str] | None:
        """The fact a choice on the page disagrees with, and the option it says.

        Only a choice is judged, since a click can land by accident and typing
        cannot. The fact is the one an active pattern names, else the one the
        worker filled this question from. None when no fact speaks to it.
        """
        field = find_field(fields, question)
        if field is None or is_follow_up_box(field) or is_sensitive_field(field):
            return None
        options = self.options_for(fields, field)
        target = dict(field) | {"label": question, "options": options}
        resolution = Resolver(self.ctx.knowledge).resolve(target)
        key = self.state.tier_fact.get(question)
        if resolution is None and key is not None:
            rendering = Resolver(self.ctx.knowledge).render_fact(key, target, question)
            if rendering is not None:
                resolution = AnswerResolution(
                    rendering.value, "pattern", "fact", fact_key=key,
                    formatter=rendering.formatter,
                )
        if resolution is None or resolution.fact_key is None or MULTI in resolution.value:
            return None
        wanted = choose_option(options, resolution.value) if options else None
        if wanted is not None:
            same = normalize_answer_question(wanted) == normalize_answer_question(value)
            return None if same else (resolution.fact_key, wanted)
        held, expected = polarity(value), polarity(resolution.value)
        if held is not None and expected is not None and held != expected:
            return resolution.fact_key, resolution.value
        return None

    def flag_follow_ups(self, fields: list[dict[str, Any]], held: dict[str, str]) -> None:
        """Say when an answer you changed makes a follow-up the worker answered N/A apply."""
        flagged: set[str] = set()
        for item in fields:
            question = _question_for(item)
            condition = follow_up_condition(question)
            if (
                condition is None
                or not is_follow_up_box(item)
                or self.state.tier_resolver.get(question) != NOT_APPLICABLE.resolver_id
                or held.get(question) != NOT_APPLICABLE.value
                or answer_above(fields, item) != condition
            ):
                continue
            flagged.add(question)
            if question not in self.state.na_flagged:
                echo("review", question, NOT_APPLICABLE.value,
                     note=f"you answered {'Yes' if condition else 'No'} above, so this applies "
                          "now: replace N/A")
        self.state.na_flagged = flagged

    def record_snapshot(self, fields: list[dict[str, Any]]) -> None:
        writing = False  # a long-form answer changed: saved at once, not at the run's end
        withheld = dict(self.state.withheld)
        flagged = self.state.na_flagged
        held: dict[str, str] = {}  # question -> what the page shows for it
        contradicted: set[str] = set()
        for index, item in enumerate(snapshot_entries(self.page, fields)):
            question = str(item["question"])
            value = str(item["answer_text"])
            if value:
                held[question] = value.strip()
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
            if value and source == "manual":
                # A choice a fact contradicts is said at once and never saved:
                # it is most often a stray click. Put it back, or set the fact.
                if self.state.withheld.get(control_key) == value:
                    contradicted.add(question)
                    continue
                self.state.withheld.pop(control_key, None)
                against = self.contradiction(fields, question, value)
                if against is not None:
                    self.state.withheld[control_key] = value
                    contradicted.add(question)
                    fact_key, says = against
                    echo("review", question, value,
                         note=f"{fact_key} says {says}; not saved. Put it back, or set "
                              "the fact if this is right")
                    value = ""  # the page no longer holds an answer worth saving
            else:
                self.state.withheld.pop(control_key, None)
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
            origin = self.state.origins.get(question)
            if item.get("form") == "long" and origin is not None:
                draft, started_from = origin
                self.state.collected[control_key]["provenance"] = spans(
                    value, draft=draft, previous=started_from
                )
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
                    | (
                        {"provenance": self.state.collected[control_key]["provenance"]}
                        if "provenance" in self.state.collected[control_key] else {}
                    )
                )
                writing |= item.get("form") == "long"
            # Echo what the user typed or chose so they can see it was picked up.
            if source == "manual" and self.state.announced.get(control_key) != value:
                self.state.announced[control_key] = value
                echo("picked up", question, value)
        if writing and not self.ctx.dry_run:
            # Writing is kept as it is written: a run that is killed loses none of it.
            with contextlib.suppress(httpx.HTTPError, RuntimeError):
                self._save_now(quiet=True)
        self.flag_follow_ups(fields, held)
        if self.state.withheld != withheld or self.state.na_flagged != flagged:
            with contextlib.suppress(Error):
                highlight_unfilled(
                    self.page, fields, self.state.outlined | self.state.na_flagged | contradicted
                )

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
            self.answer_remote_facts(current)
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
