"""The fixer."""

from __future__ import annotations

import contextlib
from dataclasses import asdict
from typing import Any

import httpx
from playwright.sync_api import Error

from personal_os_worker.core.config import console
from personal_os_worker.jobs.audit import (
    Finding,
    classify_unknown,
)
from personal_os_worker.jobs.browser import (
    cdp_target_id,
    inspect_fields,
    page_evidence,
    registrable_site,
)
from personal_os_worker.jobs.fix.lane import Problem
from personal_os_worker.jobs.fix.queue import State
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.context import (
    CODE_CAUSES,
    TRIED,
    _question_for,
    echo,
)


class FixerMixin(SessionBase):
    # ----- the fixer ------------------------------------------------------------

    def problem_key(self, cause: str, question: str) -> str:
        """One problem per cause, site, and structure; a wall is one per site."""
        site = registrable_site(self.page.url)
        if cause in {"wall", "posting_not_found"}:
            return f"{cause}|{site}"
        if cause == "unknown_control":
            return f"{cause}|{site}|{normalize_answer_question(question)}"
        structure = self._structure_for(cause, question)
        key = f"{cause}|{site}|{structure}"
        if cause == "format_error" and question:
            key += f"|{question}"
        return key

    def _structure_for(self, cause: str, question: str) -> str:
        """The widget's structural identity: identical widgets share a problem."""
        if cause == "unknown_control":
            return normalize_answer_question(question)
        from personal_os_worker.jobs.browser import fresh_control  # noqa: F401
        from personal_os_worker.jobs.registry.kinds import control_key
        from personal_os_worker.jobs.repair.repairer import find_field

        try:
            fields = inspect_fields(self.page)
            field = find_field(fields, question)
        except Exception:
            field = None
        if field is not None:
            return control_key(field)
        return normalize_answer_question(question)

    def escalate(self, findings: list[Finding]) -> None:
        """Hand the fixer what only code can settle; it works while the run goes on.

        A widget no kind captures and a question that cannot be read go
        straight to it; a format or control error only once the repair lane
        has had its turn; a wall once the Apply control, the gates, and the
        navigator have. A missing fact, a pattern gap, and prose are never code.
        Each problem is one failure-issue row; an unsolved or fixed row is never
        handed over again — it waits for Retry or a new fixer API version.
        """
        if self.ctx.fixes is None or self.ctx.dry_run:
            return
        for finding in findings:
            if finding.cause not in CODE_CAUSES:
                continue
            if (finding.evidence or {}).get("unlabelled"):
                continue  # no learned file can give a widget a question
            key = self.problem_key(finding.cause, finding.question)
            if key in self.state.fix_keys:
                continue
            status = self._problem_issue(finding, key)
            if status in {"unsolved", "fixed"}:
                continue
            problem = self.problem_for(finding, key)
            if problem is not None and self.ctx.fixes.submit(problem):
                self.state.fix_keys.add(key)

    def _problem_issue(self, finding: Finding, key: str) -> str | None:
        """The problem's one failure-issue row, upserted through persist_step;
        its status, or None when it could not be read."""
        from personal_os_worker.jobs.sites import failure_signature

        evidence = dict(finding.evidence or {})
        fingerprint = "problem|" + key
        failure = {
            "signature": failure_signature(self.ctx.ats, finding.cause, fingerprint),
            "category": finding.cause,
            "question": finding.question,
            "field_label": finding.question,
            "control_kind": evidence.get("role"),
            "field_fingerprint": fingerprint,
            "fact_key": finding.fact_key,
            "error_message": finding.note,
            "dom_signals": {
                name: value
                for name, value in (evidence | {"missing_fact": finding.missing_fact}).items()
                if name != "html" and value is not None
            },
        }
        try:
            with contextlib.suppress(Error):
                failure["artifact_paths"] = self.upload_evidence(
                    page_evidence(self.page, inspect_fields(self.page),
                                  console_errors=self.ctx.console_errors)
                )
            issues = self.persist_step(
                self.ctx.run_id,
                {"step_number": self.state.step, "url": self.page.url,
                 "failures": [failure]},
            )
        except httpx.HTTPError:
            return None
        issue = next((row for row in issues
                      if str(row.get("signature") or "") == failure["signature"]),
                     issues[0] if issues else {})
        issue_id = issue.get("id")
        if issue_id is not None:
            self.state.issue_ids[key] = str(issue_id)
        return str(issue.get("status") or "open")

    def problem_state(self, problem: Problem) -> State:
        """This problem's cause re-checked on the page as it is now, on the run
        thread: still failing ("persists"), a kept fix answers it ("answered"),
        or it is no longer on the page ("gone")."""
        from personal_os_worker.jobs.browser import (
            inspect_unknown_controls,
            looks_like_application_form,
        )
        from personal_os_worker.jobs.navigate import is_posting
        from personal_os_worker.jobs.registry.kinds import learned_question
        from personal_os_worker.jobs.repair.repairer import find_field, repair_strategy
        from personal_os_worker.jobs.resolve import Resolver

        try:
            fields = inspect_fields(self.page)
        except Error:
            return "persists"
        if problem.target.get("kind") == "route":
            if problem.target.get("goal") == "form":
                return "gone" if looks_like_application_form(fields) else "persists"
            return "gone" if is_posting(self.page, self.ctx.title) else "persists"
        field = find_field(fields, problem.question)
        if problem.cause == "unknown_control":
            if field is not None:
                return "answered"
            captured = {
                normalize_answer_question(_question_for(item)) for item in fields
            }
            try:
                for finding in classify_unknown(
                    inspect_unknown_controls(self.page), captured
                ):
                    if normalize_answer_question(finding.question) == (
                        normalize_answer_question(problem.question)
                    ):
                        return (
                            "gone"
                            if (finding.evidence or {}).get("unlabelled")
                            else "persists"
                        )
            except Error:
                return "persists"
            return "gone"
        if problem.cause == "detection_error":
            if field is not None:
                return "persists"
            radios = self.page.locator('input[type="radio"]')
            for index in range(radios.count()):
                with contextlib.suppress(Error):
                    text, _reader = learned_question(
                        radios.nth(index), registrable_site(self.page.url)
                    )
                    if text:
                        return "answered"
            return "gone"
        if field is None:
            return "gone"
        try:
            resolution = Resolver(self.ctx.knowledge).resolve(
                dict(field) | {"label": problem.question,
                               "options": field.get("options") or []}
            )
        except Exception:
            return "persists"
        if resolution is None or resolution.value is None:
            return "persists"
        if problem.cause == "control_error":
            with contextlib.suppress(Exception):
                if repair_strategy(self.page, field, str(resolution.value)) is not None:
                    return "answered"
            return "persists"
        return "answered"

    def problem_for(self, finding: Finding, key: str) -> Problem | None:
        try:
            current = inspect_fields(self.page)
            files = page_evidence(self.page, current, console_errors=self.ctx.console_errors)
        except Error:
            return None
        if finding.cause in {"wall", "posting_not_found"}:
            target: dict[str, Any] = {
                "kind": "route", "goal": "posting" if finding.cause == "posting_not_found"
                else "form", "title": self.ctx.title, "company": self.ctx.company,
            }
        else:
            target = {"kind": "field", "question": finding.question}
        from personal_os_worker.jobs.fix.sanitize import sanitize_evidence

        return Problem(
            key=key,
            cause=finding.cause,
            question=finding.question,
            note=finding.note,
            target=target,
            site=registrable_site(self.page.url),
            evidence=dict(finding.evidence or {}) | {"url": self.page.url},
            tried=TRIED.get(finding.cause, ()),
            fact_key=finding.fact_key,
            issue_id=self.state.issue_ids.get(key),
            run_id=self.ctx.run_id,
            structure=self._structure_for(finding.cause, finding.question),
            page_files=sanitize_evidence(files, dict(self.ctx.knowledge.facts)),
        )

    def probe_args(self) -> dict[str, Any]:
        """What the probe needs to find this run's tab and answer from its facts."""
        return {
            "cdp_url": self.ctx.cdp_url,
            "target_id": cdp_target_id(self.page),
            "page_url": self.page.url,
            "knowledge": asdict(self.ctx.knowledge),
        }

    def safe_point(self) -> None:
        """Between polls and after an audit: the queue tries a waiting fix here."""
        if self.ctx.fixes is None:
            return
        self.ctx.fixes.at_safe_point(self.probe_args, self.problem_state)

    def report_review(self) -> None:
        """List the long-form answers that came from a draft, once per step."""
        if not self.state.review:
            return
        console.print()
        console.print("  [bold]Review before submitting[/bold] (edit in the browser if you like):")
        for question, label in self.state.review:
            echo("review", question, note=label)
        self.state.review.clear()
