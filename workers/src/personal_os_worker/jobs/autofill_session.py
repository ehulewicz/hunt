"""The interactive part of `os hunt apply`, without any terminal prompts.

The terminal only reports. A field fills from a fact: through the pattern you
accepted for its question, else through the archive's translation, else through
the model naming the fact (design §8, §14). Every textarea is seeded with
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Error, Page
from rich.markup import escape

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.tables import console
from personal_os_worker.jobs.adapters import failure_signature
from personal_os_worker.jobs.answers import draft_contextual_answer
from personal_os_worker.jobs.archive.lookup import answer_key
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
    plan_autofill,
    reveal_application_form,
    sanitized_evidence,
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
from personal_os_worker.jobs.keys import KeyWatcher, NoKeys
from personal_os_worker.jobs.page_state import (
    ApplicationPageState,
    AshbyNavigation,
    capture_ashby_navigation,
    classify_application_page,
)
from personal_os_worker.jobs.registry.events import planned_events, unresolved_events
from personal_os_worker.jobs.resolve import Knowledge, Miss, Resolver, VerdictResolver
from personal_os_worker.jobs.short_answers import ShortQuestion, Verdict, draft_short_answers


class SubmittedByUser(Exception):
    """Ctrl+A: the user says the application went in."""


class SkippedByUser(Exception):
    """Ctrl+D: the user passes on this listing; it leaves the board without an application."""


Drafter = Callable[..., str | None]
ShortDrafter = Callable[..., dict[str, Verdict]]
# Archived answers that are writing rather than a fact.
PROSE_LENGTH = 80
LetterDrafter = Callable[..., LetterSlots | None]
LetterRenderer = Callable[..., Path]


@dataclass
class SessionContext:
    api: WorkerApi
    run_id: str
    adapter_name: str
    browser: VisibleBrowser
    page: Page
    console_errors: list[str]
    knowledge: Knowledge
    resume_json: dict[str, Any]
    resume_pdf: Path
    company: str
    title: str
    description: str | None
    transcript_pdf: Path | None = None
    dry_run: bool = False
    drafter: Drafter = draft_contextual_answer
    short_drafter: ShortDrafter = draft_short_answers
    letter_drafter: LetterDrafter = draft_slots
    letter_template: Callable[[], LetterTemplate] = load_template
    editor: Callable[[Path], None] = edit_in_vscode
    letter_renderer: LetterRenderer = letter_pdf
    letter_dir: Path | None = None  # where the editable letter and its PDF live
    use_claude: bool = True
    poll_seconds: float = 1.0
    keys: KeyWatcher | NoKeys = field(default_factory=KeyWatcher)


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
    asked_claude: set[str] = field(default_factory=set)  # questions already put to Claude
    misses: dict[str, Miss] = field(default_factory=dict)  # answer key -> why unanswered
    needed: dict[str, str] = field(default_factory=dict)  # question -> fact the model named
    step: int = 0
    form_seen: bool = False
    navigation: AshbyNavigation | None = None


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

TAG_STYLES = {
    "pattern": "green",
    "archive": "yellow",
    "claude": "magenta",
    "source": "dim",
    "consent": "dim",
    "long-form": "cyan",
    "prose": "cyan",
    "letter": "cyan",
    "picked up": "cyan",
    "saved": "green",
    "kept": "dim",
    "left": "yellow",
    "optional": "dim",
    "review": "yellow",
    "failed": "red",
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
        self.timings = AutofillTimings(report=lambda line: console.print(line, style="dim"))

    # ----- reporting -------------------------------------------------------

    def upload_evidence(self, evidence: dict[str, bytes]) -> dict[str, str]:
        with self.timings.measure("evidence_upload", step=self.state.step):
            return self._upload_evidence(evidence)

    def _upload_evidence(self, evidence: dict[str, bytes]) -> dict[str, str]:
        return {
            name: self.ctx.api.upload_autofill_artifact(
                self.ctx.run_id, name=name, content=content, content_type=content_type_for(name)
            )
            for name, content in evidence.items()
        }

    def report_page_failure(
        self, category: str, message: str, *, artifact_paths: dict[str, str] | None = None
    ) -> None:
        if artifact_paths is None:
            captured = inspect_fields(self.page)
            artifact_paths = self.upload_evidence(
                sanitized_evidence(self.page, captured, console_errors=self.ctx.console_errors)
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
                            self.ctx.adapter_name, category, fingerprint
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
            if self.state.navigation is None and self.ctx.adapter_name == "ashby":
                self.state.navigation = capture_ashby_navigation(self.page)
            page_state = classify_application_page(self.page, fields, self.state.navigation)
            if page_state == ApplicationPageState.SUBMITTED:
                return None
            if page_state == ApplicationPageState.FORM:
                self.state.form_seen = True
                for item in fields:
                    item["ats"] = self.ctx.adapter_name
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
                        item["ats"] = self.ctx.adapter_name
                    return refreshed
            if page_state == ApplicationPageState.LOADING:
                unsettled_since = unsettled_since or time.monotonic()
                if time.monotonic() - unsettled_since < 5:
                    self.sleep()
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

    def fill_deterministic(self, fields: list[dict[str, Any]]) -> dict[str, Any]:
        resolver = Resolver(self.ctx.knowledge)
        fields[:], plan = plan_autofill(self.page, resolver=resolver)
        self.state.misses.update(resolver.misses)
        for item in fields:
            item["ats"] = self.ctx.adapter_name
        if not self.ctx.dry_run:
            # Prose controls are seeded by fill_long_form, which knows their source.
            plan = type(plan)(
                tuple(
                    fill for fill in plan.fills
                    if not is_long_form_field(fields[fill.index])
                ),
                plan.unanswered_required_questions,
            )
        console.print()
        console.rule(f"[bold]Step {self.state.step + 1}[/bold]", align="left")
        console.print(
            f"  {len(fields)} controls · {len(plan.fills)} planned deterministically · "
            f"{len(plan.unanswered_required_questions)} required still open"
        )
        if self.ctx.dry_run:
            self.ctx.api.persist_autofill_step(
                self.ctx.run_id,
                {
                    "step_number": self.state.step,
                    "url": self.page.url,
                    "fill_events": planned_events(fields, plan, misses=self.state.misses),
                },
            )
            return {"filled_fields": [], "skipped_fields": [], "failures": [], "plan": plan}
        before = sanitized_evidence(self.page, fields, console_errors=self.ctx.console_errors)
        result = apply_autofill(
            self.page,
            fields,
            plan,
            resume_path=self.ctx.resume_pdf,
            transcript_path=self.ctx.transcript_pdf,
            assert_active=lambda: None,
        )
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
                        self.ctx.adapter_name, "required_field_empty", fingerprint
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
        after = sanitized_evidence(
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
        self.ctx.api.persist_autofill_step(
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
                    skip=lambda item: is_long_form_field(item) or is_cover_letter_field(item),
                    misses=self.state.misses,
                ),
            },
        )
        result["artifact_paths"] = artifact_paths
        return result

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
        # Once per question per run: a re-render that restarts the step, or a
        # later step showing the same question, must not ask again.
        questions = [
            question
            for question in _claude_questions(current)
            if question.question not in self.state.asked_claude
        ]
        if not questions:
            return
        self.state.asked_claude.update(question.question for question in questions)
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
        claude_result = apply_autofill(
            self.page, claude_fields, plan, resume_path=None, assert_active=lambda: None
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
        """Your earlier writing on similar questions: approved prose, then the archive's."""
        writing = [
            {"question": item.get("question_label"), "answer_text": item.get("text")}
            for item in self.ctx.knowledge.prose
        ] + [
            {"question": item.get("question"), "answer_text": item.get("answer_text")}
            for item in self.ctx.knowledge.archive
            if str(item.get("reuse_policy")) == "contextual"
            or len(str(item.get("answer_text") or "")) >= PROSE_LENGTH
        ]
        return similar_answers(question, writing)

    def seed_for(self, question: str, *, limit: int | None = None) -> tuple[str, str, str, str]:
        """Return (seed text, seed source, resolver id, human note)."""
        key = answer_key(question)
        approved = next(
            (
                item
                for item in self.ctx.knowledge.prose
                if answer_key(str(item.get("question_label") or "")) == key
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
            facts=self.ctx.knowledge.disclosable(),
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

    def why_empty(self, question: str) -> str | None:
        if question in self.state.needed:
            return f"needs a fact: {self.state.needed[question]}"
        miss = self.state.misses.get(answer_key(question))
        return miss.note or None if miss is not None else None

    def report_left(self) -> None:
        """Every field still empty, required first, outlined on the page (design §10.2).

        Optional fields count: nothing is left blank unless you leave it. What
        the worker knows about why each is empty — no fact yet, an old answer
        that disagrees, a missing fact the model named — is said beside it.
        """
        try:
            fields = inspect_fields(self.page)
            audit = audit_filled_form(fields)
        except Error:
            return
        left = [(question, True) for question in audit["required_unfilled"]]
        left += [(question, False) for question in audit["optional_unfilled"]]
        if not left:
            console.print("  [dim]every field is filled[/dim]")
            return
        with contextlib.suppress(Error):
            highlight_unfilled(self.page)
        required = sum(1 for _, is_required in left if is_required)
        console.print()
        console.print(
            f"  [bold]Left for you[/bold] ({required} required, {len(left) - required} optional,"
            " outlined on the page):"
        )
        for question, is_required in left:
            reason = self.why_empty(question)
            echo("left" if is_required else "optional", question, note=reason)

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
            if source in {"pattern", "archive", "model"} and question in self.state.tier_resolver:
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
                if self.ctx.use_claude:
                    with self.timings.measure("short_drafts", step=self.state.step):
                        self.fill_with_claude(fields, result)
                with self.timings.measure("long_form", step=self.state.step):
                    self.fill_long_form(fields)
                with self.timings.measure("cover_letter_review", step=self.state.step):
                    self.fill_cover_letter(fields)
                self.report_left()
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
