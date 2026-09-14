"""The interactive part of `os hunt apply`, without any terminal prompts.

The terminal only reports. Every textarea is seeded on the page with its best
answer and the ones that came from a Claude draft are listed for review.
Everything else the deterministic planner could not fill is typed on the page
by the user; the worker polls the page, saves every answer it can see, and
watches for a visible Submit control. The user owns Next and Submit; the run
ends when the page reports submission or when the user interrupts, and both
paths save the collected answers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Error, Page
from rich.markup import escape

from personal_os_worker.adapters import failure_signature
from personal_os_worker.answers import answer_match, draft_contextual_answer
from personal_os_worker.api import WorkerApi
from personal_os_worker.autofill import FormFill, is_resume_field, is_sensitive_field
from personal_os_worker.browser import (
    BrowserClosedError,
    VisibleBrowser,
    apply_autofill,
    combobox_options,
    fresh_control,
    has_submit_control,
    inspect_fields,
    is_browser_closed_error,
    looks_like_application_form,
    plan_autofill,
    reveal_application_form,
    sanitized_evidence,
    set_registered_value,
    snapshot_values,
    verify_control,
)
from personal_os_worker.drafts import similar_answers
from personal_os_worker.keys import KeyWatcher, NoKeys
from personal_os_worker.patterns import resolve_pattern_answer
from personal_os_worker.short_answers import ShortQuestion, draft_short_answers
from personal_os_worker.tables import console

SUBMITTED_MARKERS = (
    "thank you for applying",
    "thanks for applying",
    "application has been submitted",
    "application was submitted",
    "application submitted",
    "received your application",
    "submitted successfully",
)

class SubmittedByUser(Exception):
    """Ctrl+A: the user says the application went in."""


Drafter = Callable[..., str | None]
ShortDrafter = Callable[..., dict[str, str]]


@dataclass
class SessionContext:
    api: WorkerApi
    run_id: str
    adapter_name: str
    browser: VisibleBrowser
    page: Page
    console_errors: list[str]
    profile: dict[str, Any]
    answers: list[dict[str, Any]]
    scoped: list[dict[str, Any]]
    resume_json: dict[str, Any]
    resume_pdf: Path
    company: str
    title: str
    description: str | None
    transcript_pdf: Path | None = None
    dry_run: bool = False
    drafter: Drafter = draft_contextual_answer
    short_drafter: ShortDrafter = draft_short_answers
    use_claude: bool = True
    poll_seconds: float = 1.0
    keys: KeyWatcher | NoKeys = field(default_factory=KeyWatcher)


@dataclass
class SessionState:
    review: list[tuple[str, str]] = field(default_factory=list)  # (question, source) long-form
    collected: dict[str, dict[str, Any]] = field(default_factory=dict)
    seeds: dict[str, tuple[str, str]] = field(default_factory=dict)  # question -> (text, source)
    planned: dict[str, str] = field(default_factory=dict)  # question -> planned value
    announced: dict[str, str] = field(default_factory=dict)  # question -> value shown
    saved: dict[str, str] = field(default_factory=dict)  # question -> value in the library
    warnings: set[str] = field(default_factory=set)
    tier_answers: dict[str, str] = field(default_factory=dict)  # question -> pattern/model answer
    tier_source: dict[str, str] = field(default_factory=dict)  # question -> "pattern" | "model"
    asked_claude: set[str] = field(default_factory=set)  # questions already put to Claude
    step: int = 0


def page_signature(page: Page, fields: list[dict[str, Any]]) -> tuple[str, tuple[str, ...]]:
    return page.url, tuple(str(item.get("fingerprint")) for item in fields)


def looks_submitted(page: Page) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=5_000).casefold()
    except Error:
        return False
    return any(marker in body for marker in SUBMITTED_MARKERS)


def preview(value: str, *, limit: int = 100) -> str:
    """One terminal line of a value, however long the textarea answer is."""
    flat = " ".join(value.split())
    return flat if len(flat) <= limit else flat[: limit - 3].rstrip() + "..."


TAG_WIDTH = 10
QUESTION_WIDTH = 42
VALUE_WIDTH = 60

TAG_STYLES = {
    "profile": "dim",
    "library": "dim",
    "pattern": "magenta",
    "claude": "magenta",
    "long-form": "cyan",
    "picked up": "cyan",
    "saved": "green",
    "kept": "dim",
    "left": "yellow",
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


def _applied_value(field: dict[str, Any], fill: FormFill) -> str | None:
    """What actually ends up on the page for a planned fill.

    A radio/checkbox fill's `value` is the raw answer text used to pick the
    option (e.g. "No"), not the option's own label (e.g. "No - never"); the
    control that gets checked is always this field's own option.
    """
    if fill.value is None:
        return None
    if str(field.get("type") or "").casefold() in {"radio", "checkbox"}:
        return str(field.get("label") or field.get("value") or fill.value)
    return fill.value


def _claude_questions(fields: list[dict[str, Any]]) -> list[ShortQuestion]:
    """Unfilled, non-sensitive, non-textarea, non-file controls left for Claude.

    Radio/checkbox groups with no option checked (options = the group's option
    labels), selects and typeaheads with a known menu (their options), and
    required text inputs (no options).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    questions: dict[str, ShortQuestion] = {}
    for item in fields:
        field_type = str(item.get("type") or "").casefold()
        tag = str(item.get("tag") or "").casefold()
        if field_type in {"file", "submit", "button", "reset", "hidden"} or tag in {
            "textarea",
            "button",
        }:
            continue
        if is_sensitive_field(item):
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

    # ----- reporting -------------------------------------------------------

    def upload_evidence(self, evidence: dict[str, bytes]) -> dict[str, str]:
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

    def form_is_gone(self) -> bool:
        """The page has left the form for a confirmation, so nothing on it is ours."""
        try:
            fields = inspect_fields(self.page)
        except Error:
            return False
        return not looks_like_application_form(fields) and looks_submitted(self.page)

    def save_now(self) -> int:
        """Push every picked-up answer that changed since the last save to the library."""
        # Profile facts and unchanged library answers are already stored; only
        # what the user typed or chose, or a Claude draft they accepted, is new.
        pending = [
            item
            for question, item in self.state.collected.items()
            if item["source"] in {"manual", "model", "pattern"}
            and self.state.saved.get(question) != item["answer_text"]
        ]
        for item in pending:
            self.ctx.api.create_answer(
                question=str(item["question"]),
                answer_text=str(item["answer_text"]),
                approved=True,
                reuse_policy=str(item["reuse_policy"]),
            )
            self.state.saved[str(item["question"])] = str(item["answer_text"])
            echo("saved", str(item["question"]), str(item["answer_text"]))
        if not pending:
            console.print("  [dim]nothing new to save[/dim]")
        return len(pending)

    # ----- form discovery --------------------------------------------------

    def wait_for_form(self) -> list[dict[str, Any]] | None:
        """Return the form's fields, or None once the page shows the application went in."""
        wall_reported = False
        while True:
            self.page = self.ctx.browser.active_page(self.page)
            reveal_application_form(self.page)
            fields = inspect_fields(self.page)
            if looks_like_application_form(fields) or has_submit_control(self.page):
                for item in fields:
                    item["ats"] = self.ctx.adapter_name
                return fields
            if self.state.step > 0 and looks_submitted(self.page):
                return None
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

    def exact_answer(self, question: str) -> dict[str, Any] | None:
        matched = answer_match(question, self.ctx.answers)
        if matched is None or str(matched.get("reuse_policy") or "exact") != "exact":
            return None
        return matched

    def pattern_fallback(self, question: str) -> str | None:
        answer = resolve_pattern_answer(
            question,
            rules=self.ctx.answers,
            profile=self.ctx.profile,
            resume=self.ctx.resume_json,
            company=self.ctx.company,
        )
        if answer is not None:
            self.state.tier_answers[question] = answer
            self.state.tier_source[question] = "pattern"
        return answer

    def fill_deterministic(self, fields: list[dict[str, Any]]) -> dict[str, Any]:
        fields[:], plan = plan_autofill(
            self.page,
            profile=self.ctx.profile,
            approved_answers=self.ctx.answers,
            fallback=self.pattern_fallback,
        )
        for item in fields:
            item["ats"] = self.ctx.adapter_name
        if not self.ctx.dry_run:
            # Textareas are seeded by fill_long_form, which knows their source.
            plan = type(plan)(
                tuple(fill for fill in plan.fills if fields[fill.index].get("tag") != "textarea"),
                plan.unanswered_required_questions,
            )
        console.print()
        console.rule(f"[bold]Step {self.state.step + 1}[/bold]", align="left")
        console.print(
            f"  {len(fields)} controls · {len(plan.fills)} planned · "
            f"{len(plan.unanswered_required_questions)} required left for you"
        )
        if self.ctx.dry_run:
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
            applied = _applied_value(fields[fill.index], fill)
            if applied is not None and question:
                self.state.planned[question] = applied
                if question in self.state.tier_answers:
                    self.state.tier_answers[question] = applied
                    echo("pattern", question, applied)
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
        console.print(f"  [dim]asking Claude about {len(questions)} question(s)[/dim]")
        drafted = self.ctx.short_drafter(
            questions,
            posting={
                "company": self.ctx.company,
                "role": self.ctx.title,
                "description": self.ctx.description,
            },
            resume=self.ctx.resume_json,
            profile=self.ctx.profile,
            library=self.ctx.answers,
        )
        if not drafted:
            console.print("  [dim]Claude had no confident answers[/dim]")
            return
        if self.form_is_gone():
            return
        claude_fields, plan = plan_autofill(
            self.page, profile={}, approved_answers=[], fallback=drafted.get
        )
        claude_result = apply_autofill(
            self.page, claude_fields, plan, resume_path=None, assert_active=lambda: None
        )
        for fill in plan.fills:
            question = _question_for(claude_fields[fill.index])
            applied = _applied_value(claude_fields[fill.index], fill)
            if applied is not None and question in drafted:
                self.state.tier_answers[question] = applied
                self.state.tier_source[question] = "model"
                echo("claude", question, applied)
        result["failures"].extend(claude_result["failures"])

    # ----- long-form drafts ------------------------------------------------

    def seed_for(self, question: str) -> tuple[str, str, str, list[dict[str, Any]]]:
        """Return (seed text, seed source, human note, similar answers)."""
        matched = answer_match(question, self.ctx.answers)
        similar = similar_answers(question, self.ctx.answers)
        if matched is not None and str(matched.get("reuse_policy") or "exact") == "exact":
            return (
                str(matched.get("answer_text") or "").strip(),
                "answer_library",
                "exact library answer (edit freely)",
                similar,
            )
        base = str(matched.get("answer_text") or "").strip() if matched else None
        draft = self.ctx.drafter(
            question=question,
            base=base or None,
            posting={
                "company": self.ctx.company,
                "role": self.ctx.title,
                "description": self.ctx.description,
            },
            resume=self.ctx.resume_json,
            profile=self.ctx.profile,
            scoped_variants=[*self.ctx.scoped, *similar],
        )
        if draft:
            note = (
                "claude draft, based on the contextual library answer (edit freely)"
                if base
                else "claude draft tailored to this posting (edit freely)"
            )
            return draft, "model", note, similar
        if base:
            return base, "answer_library", "contextual library answer; claude draft failed", similar
        return "", "empty", "no library match and no claude draft; write from scratch", similar

    def fill_long_form(self, fields: list[dict[str, Any]]) -> None:
        """Seed every textarea with its best answer and say which ones deserve a look.

        An exact library answer goes in as is. Otherwise a Claude draft grounded
        in the posting goes in and is flagged for review. A textarea that
        already holds text is kept, and one with nothing to offer is left alone.
        """
        for item in fields:
            if item.get("tag") != "textarea" or is_sensitive_field(item):
                continue
            question = str(item.get("label") or "").strip()
            if not question:
                continue
            if item.get("filled") is True:
                echo("kept", question, note="already has text")
                continue
            seed, source, note, _similar = self.seed_for(question)
            self.state.seeds[question] = (seed, source)
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
            self.state.announced[question] = seed
            label = "claude draft" if source == "model" else "library"
            echo("long-form", question, seed, note=label)
            if source == "model":
                self.state.review.append((question, label))

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
        for question, item in snapshot_values(self.page, fields).items():
            value = str(item["answer_text"])
            seed, seed_source = self.state.seeds.get(question, ("", "empty"))
            matched = answer_match(question, self.ctx.answers)
            if question in self.state.tier_answers and value == self.state.tier_answers[question]:
                source = self.state.tier_source[question]
            elif seed_source in {"answer_library", "model"} and value == seed:
                source = seed_source
            elif matched is not None and value == str(matched.get("answer_text") or "").strip():
                source = "answer_library"
            elif self.state.planned.get(question) == value:
                source = "jobs_profile"
            else:
                source = "manual"
            if matched is not None:
                policy = str(matched.get("reuse_policy") or "exact")
            else:
                policy = "contextual" if item["control_kind"] == "textarea" else "exact"
            self.state.collected[question] = {
                "question": question,
                "answer_text": value,
                "source": source,
                "reuse_policy": policy,
            }
            # Echo what the user typed or chose so they can see it was picked up.
            if source == "manual" and self.state.announced.get(question) != value:
                self.state.announced[question] = value
                echo("picked up", question, value)

    def wait_and_advance(self, fields: list[dict[str, Any]]) -> None:
        """Collect manual answers while the user owns page navigation and submission."""
        prior = page_signature(self.page, fields)
        submit_reported = False
        while True:
            self.page = self.ctx.browser.active_page(self.page)
            current = inspect_fields(self.page)
            if page_signature(self.page, current) != prior:
                return
            if not looks_like_application_form(current) and looks_submitted(self.page):
                return
            self.record_snapshot(current)
            if has_submit_control(self.page) and not submit_reported:
                console.print()
                console.print("  [bold]Submit found[/bold] — press it in the browser when ready.")
                submit_reported = True
            self.sleep()

    # ----- driver ----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        console.print(
            "[dim]Ctrl+S saves picked-up answers · Ctrl+A marks submitted and ends · "
            "Ctrl+C stops and saves[/dim]"
        )
        with self.ctx.keys:
            return self._run()

    def _run(self) -> dict[str, Any]:
        submitted = False
        try:
            while True:
                fields = self.wait_for_form()
                if fields is None:
                    console.print("The page reports the application was submitted.")
                    submitted = True
                    break
                self.emit_warnings(fields)
                result = self.fill_deterministic(fields)
                if self.ctx.dry_run:
                    return {"final_url": self.page.url, "answers": [], "dry_run": True}
                if self.ctx.use_claude:
                    self.fill_with_claude(fields, result)
                self.fill_long_form(fields)
                self.report_review()
                self.wait_and_advance(fields)
                self.state.step += 1
        except BrowserClosedError:
            console.print("The browser or application tab was closed. Ending autofill.")
        except SubmittedByUser:
            console.print("Marked as submitted.")
            self.save_now()
            return {
                "final_url": self.page.url,
                "answers": list(self.state.collected.values()),
                "submitted": True,
            }
        except KeyboardInterrupt:
            console.print("Stopped. The application tab stays open in the browser.")
            try:
                final_url = self.page.url
            except Error:
                final_url = ""
            return {
                "final_url": final_url,
                "answers": list(self.state.collected.values()),
                "interrupted": True,
            }
        except Error as error:
            if not is_browser_closed_error(self.page, error):
                raise
            console.print("The browser or application tab was closed. Ending autofill.")
        return {
            "final_url": self.page.url,
            "answers": list(self.state.collected.values()),
            "submitted": submitted,
        }

    def answers_so_far(self) -> list[dict[str, Any]]:
        return list(self.state.collected.values())
