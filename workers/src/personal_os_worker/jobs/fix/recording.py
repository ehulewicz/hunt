"""Attempts, the brake, keeping a fix, and recording each state on the problem's issue row."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.base import BRAKE, MAX_ATTEMPTS, FixBase, Ready
from personal_os_worker.jobs.fix.keep import (
    KeepError,
    Kept,
    fixture_rel,
    learned_rel,
)
from personal_os_worker.jobs.options import normalize_answer_question


class RecordingMixin(FixBase):
    # ----- attempts, brake, keeping, recording ---------------------------------

    def _attempts_of(self, problem: lane.Problem) -> int:
        """Attempts already spent on this problem, persisted on its issue row."""
        try:
            if not problem.issue_id:
                return 0
            issue = self.api.autofill_failure(problem.issue_id) or {}
            notes = str(issue.get("verification_notes") or "")
            if notes.startswith("attempts ") and " of " in notes:
                return int(notes.split(" ", 2)[1])
        except Exception:
            return 0
        return 0

    def _record_attempt(self, problem: lane.Problem, attempt: int) -> None:
        self._issue(problem, {"status": "fixing",
                              "verification_notes": f"attempts {attempt} of {MAX_ATTEMPTS}"})

    def _keep(self, ready: Ready) -> Kept | None:
        problem = ready.problem
        chosen = ready.chosen or ready.candidates[0]
        learned = learned_rel(problem.category, chosen.stem)
        fixtures = fixture_rel(problem.category, chosen.stem)
        if ready.reuse:
            subject = f"Reuse {chosen.stem} for {problem.site}"
            summary = f"{problem.question!r} reads back with the same declaration."
        elif ready.revised:
            subject = f"Revise {chosen.stem} for {problem.site}"
            summary = ready.answer.summary
        else:
            subject = (
                f"Fix {problem.cause.replace('_', ' ')} for {problem.question!r}"
            )
            summary = ready.answer.summary
        message = (
            f"{subject}\n\n{summary}\n\n"
            f"Written and kept during run {problem.run_id or self.run_id}: checks and "
            f"the live control passed ({(ready.probe or {}).get('note')})."
        )
        try:
            kept = self.keeper(
                self.repo, message, chosen.attempt_dir, learned, fixtures,
            )
        except KeepError as error:
            ready.probe = {"ok": False, "note": f"it could not be kept: {error}"}
            return None
        ready.kept = kept
        with self._lock:
            self._kept += 1
        detail: dict[str, Any] = {
            "cause": problem.cause, "question": problem.question,
            "summary": ready.answer.summary, "attempts": ready.attempt,
            "files": [learned], "probe": ready.probe,
            "pushed": kept.pushed,
        }
        if ready.reuse:
            detail["reused"] = True
        if ready.revised:
            detail["revised"] = True
        try:
            self.api.record_code_change(
                kept.commit, origin="worker", parent=kept.parent,
                failure={"run_id": problem.run_id or self.run_id,
                         "question_key": normalize_answer_question(problem.question) or None,
                         "issue_id": problem.issue_id},
                detail=detail,
            )
        except Exception as error:
            self._report(f"kept {kept.commit[:7]}, but recording it failed: {error}")
        self._issue(problem, {"status": "fixed", "fixed_commit": kept.commit,
                              "diagnosis": ready.answer.summary,
                              "verification_notes": f"attempt {ready.attempt}: "
                              f"{(ready.probe or {}).get('note')}"
                              + ("" if kept.pushed else f"; {kept.note}")})
        self._announce(problem.key, "kept", attempt=ready.attempt,
                       note=f"{kept.commit[:7]} {ready.answer.summary}"
                       + ("" if kept.pushed else " (not pushed)"))
        # The kept file joins the running run: it is offered after the built-ins.
        if self.loader is not None:
            with suppress(Exception):
                self.loader(self.repo / learned, problem.category)
        return kept

    def _finish(self, problem: lane.Problem, state: str, note: str) -> None:
        self._issue(problem, {"status": "unsolved", "verification_notes": note[:30_000]})
        self._announce(problem.key, state, note=note)
        if state == "unsolved":
            with self._lock:
                self._consecutive_unsolved += 1
                braked = self._consecutive_unsolved >= BRAKE
                self._braked = self._braked or braked
            if braked:
                reason = (
                    f"{BRAKE} problems in a row used all {MAX_ATTEMPTS} attempts "
                    "without a kept fix"
                )
                self._report(f"fixer paused: {reason}")
                if self.publish is not None:
                    with suppress(Exception):
                        self.publish([{
                            "key": "fixer", "question": "Fixer paused",
                            "cause": "brake", "state": "paused", "note": reason,
                        }])
        else:
            with self._lock:
                self._consecutive_unsolved = 0

    def _issue(self, problem: lane.Problem, changes: dict[str, Any]) -> None:
        if problem.issue_id is None:
            return
        try:
            self.api.update_autofill_failure(problem.issue_id, changes)
        except Exception as error:
            self._report(f"could not update the failure record: {str(error)[:200]}")
            return
        if "status" in changes:
            with self._lock:
                self._recorded[problem.key] = str(changes["status"])

    def _report(self, line: str) -> None:
        with suppress(Exception):
            self.report(line)

    def _announce(self, key: str, state: str, *, attempt: int | None = None,
                  note: str | None = None) -> None:
        with self._lock:
            status = self.statuses[key]
            status.state = state
            if attempt is not None:
                status.attempt = attempt
            if note is not None:
                status.note = note
            items = [item.item() for item in self.statuses.values()]
            line = f"fix {state}: {status.question}" + (f" — {status.note}" if status.note else "")
        self._report(line)
        if self.publish is not None:
            with suppress(Exception):
                self.publish(items)
