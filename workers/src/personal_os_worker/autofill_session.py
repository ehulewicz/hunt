"""The interactive part of `os hunt autofill`, without any terminal prompts.

The terminal only reports. Long-form answers are edited in VS Code tabs and
filled when each tab closes. Everything else the deterministic planner could
not fill is typed on the page by the user; the worker polls the page, saves
every answer it can see, and advances through the one safe Next control. It
never clicks Submit. The run ends when the page leaves the form after the user
submits, or when the user interrupts, and both paths save the collected answers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Error, Page

from personal_os_worker.adapters import failure_signature
from personal_os_worker.answers import answer_match, draft_contextual_answer
from personal_os_worker.api import WorkerApi
from personal_os_worker.autofill import is_resume_field, is_sensitive_field
from personal_os_worker.browser import (
    BrowserClosedError,
    VisibleBrowser,
    apply_autofill,
    audit_filled_form,
    click_safe_transition,
    fresh_control,
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
from personal_os_worker.drafts import (
    Draft,
    DraftOpener,
    DraftProcess,
    default_opener,
    parse_draft,
    similar_answers,
    write_draft,
)
from personal_os_worker.keys import KeyWatcher, NoKeys
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

Drafter = Callable[..., str | None]


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
    draft_dir: Path
    dry_run: bool = False
    opener: DraftOpener = default_opener
    drafter: Drafter = draft_contextual_answer
    poll_seconds: float = 1.0
    keys: KeyWatcher | NoKeys = field(default_factory=KeyWatcher)


@dataclass
class SessionState:
    collected: dict[str, dict[str, Any]] = field(default_factory=dict)
    seeds: dict[str, tuple[str, str]] = field(default_factory=dict)  # question -> (text, source)
    planned: dict[str, str] = field(default_factory=dict)  # question -> planned value
    announced: dict[str, str] = field(default_factory=dict)  # question -> value shown
    saved: dict[str, str] = field(default_factory=dict)  # question -> value in the library
    warnings: set[str] = field(default_factory=set)
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
        if "save" in self.ctx.keys.wait(self.ctx.poll_seconds):
            self.save_now()

    def save_now(self) -> int:
        """Push every picked-up answer that changed since the last save to the library."""
        pending = [
            item
            for question, item in self.state.collected.items()
            if self.state.saved.get(question) != item["answer_text"]
        ]
        for item in pending:
            self.ctx.api.create_answer(
                question=str(item["question"]),
                answer_text=str(item["answer_text"]),
                approved=True,
                reuse_policy=str(item["reuse_policy"]),
            )
            self.state.saved[str(item["question"])] = str(item["answer_text"])
        if pending:
            console.print(f"  [green]saved:[/green] {len(pending)} answer(s) to the library.")
        else:
            console.print("  Nothing new to save.")
        return len(pending)

    # ----- form discovery --------------------------------------------------

    def wait_for_form(self) -> list[dict[str, Any]] | None:
        """Return the form's fields, or None once the page shows the application went in."""
        wall_reported = False
        while True:
            self.page = self.ctx.browser.active_page(self.page)
            reveal_application_form(self.page)
            fields = inspect_fields(self.page)
            if looks_like_application_form(fields):
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

    def fill_deterministic(self, fields: list[dict[str, Any]]) -> dict[str, Any]:
        fields[:], plan = plan_autofill(
            self.page, profile=self.ctx.profile, approved_answers=self.ctx.answers
        )
        for item in fields:
            item["ats"] = self.ctx.adapter_name
        if not self.ctx.dry_run:
            # Textareas go through VS Code tabs, so they are not written here.
            plan = type(plan)(
                tuple(fill for fill in plan.fills if fields[fill.index].get("tag") != "textarea"),
                plan.unanswered_required_questions,
            )
        console.print(
            f"Step {self.state.step + 1}: {len(fields)} controls, {len(plan.fills)} planned, "
            f"{len(plan.unanswered_required_questions)} required left for you."
        )
        if self.ctx.dry_run:
            return {"filled_fields": [], "skipped_fields": [], "failures": [], "plan": plan}
        before = sanitized_evidence(self.page, fields, console_errors=self.ctx.console_errors)
        result = apply_autofill(
            self.page, fields, plan, resume_path=self.ctx.resume_pdf, assert_active=lambda: None
        )
        for fill in plan.fills:
            question = str(
                fields[fill.index].get("group")
                if fields[fill.index].get("type") in {"radio", "checkbox"}
                and fields[fill.index].get("group")
                else fields[fill.index].get("label") or ""
            ).strip()
            if fill.value is not None and question:
                self.state.planned[question] = fill.value
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
        if result["failures"]:
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

    def open_drafts(self, fields: list[dict[str, Any]]) -> list[tuple[Draft, DraftProcess]]:
        opened: list[tuple[Draft, DraftProcess]] = []
        for item in fields:
            if item.get("tag") != "textarea" or is_sensitive_field(item):
                continue
            question = str(item.get("label") or "").strip()
            if not question:
                continue
            seed, source, note, similar = self.seed_for(question)
            draft = write_draft(
                self.ctx.draft_dir / f"step-{self.state.step + 1}",
                field_index=int(item["index"]),
                question=question,
                seed=seed,
                seed_source=source,
                company=self.ctx.company,
                role=self.ctx.title,
                source_note=note,
                similar=similar,
            )
            self.state.seeds[question] = (seed, source)
            opened.append((draft, self.ctx.opener(draft.path)))
            console.print(f"  [cyan]tab:[/cyan] {question} ({note.split(' (', 1)[0]})")
        if opened:
            console.print(
                f"Opened {len(opened)} question tab(s) in VS Code. Close each (:wq) to fill it."
            )
        return opened

    def fill_from_draft(self, fields: list[dict[str, Any]], draft: Draft) -> None:
        text = parse_draft(draft.path.read_text())
        if not text:
            console.print(f"  [yellow]left empty:[/yellow] {draft.question}")
            return
        item = next((f for f in fields if int(f["index"]) == draft.field_index), None)
        if item is None:
            console.print(f"  [red]lost control:[/red] {draft.question}")
            return
        try:
            set_registered_value(fresh_control(self.page, item), text)
            ok = verify_control(self.page, item, text)
        except (Error, RuntimeError) as error:
            ok = False
            console.print(f"  [red]fill failed:[/red] {draft.question}: {error}")
        if ok:
            self.state.announced[draft.question] = text
            console.print(f"  [green]filled:[/green] {draft.question}")
        else:
            self.report_page_failure(
                "readback_mismatch", f"Draft for {draft.question!r} did not register"
            )

    def await_drafts(
        self, fields: list[dict[str, Any]], opened: list[tuple[Draft, DraftProcess]]
    ) -> None:
        pending = list(opened)
        while pending:
            still_open: list[tuple[Draft, DraftProcess]] = []
            for draft, process in pending:
                if process.poll() is None:
                    still_open.append((draft, process))
                else:
                    self.fill_from_draft(fields, draft)
            pending = still_open
            if pending:
                self.sleep()

    # ----- manual completion, capture, and advance --------------------------

    def record_snapshot(self, fields: list[dict[str, Any]]) -> None:
        for question, item in snapshot_values(self.page, fields).items():
            value = str(item["answer_text"])
            seed, seed_source = self.state.seeds.get(question, ("", "empty"))
            matched = answer_match(question, self.ctx.answers)
            if seed_source in {"answer_library", "model"} and value == seed:
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
                console.print(f"  [cyan]picked up:[/cyan] {question} -> {preview(value)}")

    def wait_and_advance(
        self, fields: list[dict[str, Any]], artifact_paths: dict[str, str]
    ) -> None:
        """Poll until required fields are complete, then follow one safe Next or Submit."""
        prior = page_signature(self.page, fields)
        last_missing: tuple[str, ...] | None = None
        clicked_next = False
        transition_reported = False
        idle_polls = 0
        while True:
            self.page = self.ctx.browser.active_page(self.page)
            current = inspect_fields(self.page)
            if page_signature(self.page, current) != prior:
                return
            self.record_snapshot(current)
            missing = tuple(audit_filled_form(current)["required_unfilled"])
            if missing != last_missing:
                if missing:
                    console.print("  [yellow]left for you:[/yellow]")
                    for question in missing:
                        console.print(f"    - {question}")
                else:
                    console.print(
                        "  All required fields are filled. Press Submit in the browser when ready."
                        if clicked_next or self.ctx.adapter_name == "greenhouse"
                        else "  All required fields are filled."
                    )
                last_missing = missing
            if not missing and not clicked_next:
                clicked = click_safe_transition(self.page, kind="next")
                if clicked is not None:
                    console.print(f"Clicked {clicked}. Waiting for the next step ...")
                    clicked_next = True
                    idle_polls = 0
            elif clicked_next:
                idle_polls += 1
                if idle_polls >= 15 and not transition_reported:
                    self.report_page_failure(
                        "page_transition_failure",
                        "URL and visible control fingerprints did not change after navigation",
                        artifact_paths=artifact_paths or None,
                    )
                    transition_reported = True
                    console.print(
                        "[yellow]The form did not advance; the failure was recorded. "
                        "Use the browser to move on; I am still watching.[/yellow]"
                    )
            self.sleep()

    # ----- driver ----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        console.print("[dim]Ctrl+S saves picked-up answers now; Ctrl+C stops and saves.[/dim]")
        with self.ctx.keys:
            return self._run()

    def _run(self) -> dict[str, Any]:
        try:
            while True:
                fields = self.wait_for_form()
                if fields is None:
                    console.print("The page reports the application was submitted.")
                    break
                self.emit_warnings(fields)
                result = self.fill_deterministic(fields)
                if self.ctx.dry_run:
                    return {"final_url": self.page.url, "answers": [], "dry_run": True}
                opened = self.open_drafts(fields)
                self.await_drafts(fields, opened)
                self.wait_and_advance(fields, result.get("artifact_paths") or {})
                self.state.step += 1
        except BrowserClosedError:
            console.print("The browser or application tab was closed. Ending autofill.")
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
        return {"final_url": self.page.url, "answers": list(self.state.collected.values())}

    def answers_so_far(self) -> list[dict[str, Any]]:
        return list(self.state.collected.values())
