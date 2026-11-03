"""The fix queue: one failure at a time in the background, kept at safe points.

The run hands a problem over (`submit`) and goes on. A background thread asks
the model for one file, checks it (policy, ruff, mypy, its examples, replay),
and waits. At its next safe point the run calls `at_safe_point`, which first
serves any recheck the fixer asked for on the run thread (`Recheck`: still
failing, answered, or gone from the page), then tries the waiting candidate on
the live tab (`probe.py`), commits exactly it and its sanitized fixtures
(`keep.py`), and loads it into the running run — no reload. Only three real
attempts make a problem unsolved; anything else leaves its row open.
"""

from __future__ import annotations

import dataclasses
import queue as queues
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.background import BackgroundMixin
from personal_os_worker.jobs.fix.base import (
    BRAKE,
    MAX_ATTEMPTS,
    Candidate,
    Checker,
    FixBase,
    Prober,
    Ready,
    Recheck,
    State,
    Status,
)
from personal_os_worker.jobs.fix.gates import (
    _ROOT,
    CHECK_TIMEOUT_SECONDS,
    PROBE_TIMEOUT_SECONDS,
    _gate,
    check_candidate,
    run_probe,
)
from personal_os_worker.jobs.fix.keep import Kept, keep
from personal_os_worker.jobs.fix.recording import RecordingMixin
from personal_os_worker.jobs.fix.source import (
    _declared_sites_and_fixtures,
    _kind_candidates,
    _revision_dropped,
    _same_structure,
    _sites_arg,
    add_site,
    structure_of,
)

__all__ = [
    "BRAKE",
    "CHECK_TIMEOUT_SECONDS",
    "MAX_ATTEMPTS",
    "PROBE_TIMEOUT_SECONDS",
    "_ROOT",
    "Candidate",
    "Checker",
    "FixQueue",
    "Prober",
    "Ready",
    "Recheck",
    "State",
    "Status",
    "_declared_sites_and_fixtures",
    "_gate",
    "_kind_candidates",
    "_revision_dropped",
    "_same_structure",
    "_sites_arg",
    "add_site",
    "check_candidate",
    "run_probe",
    "structure_of",
]


class FixQueue(BackgroundMixin, RecordingMixin, FixBase):
    def __init__(
        self,
        *,
        repo: Path,
        workdir: Path,
        api: Any,
        run_id: str,
        report: Callable[[str], None],
        publish: Callable[[list[dict[str, Any]]], None] | None = None,
        runner: lane.Runner = lane.run_claude,
        prober: Prober = run_probe,
        keeper: Callable[..., Kept] = keep,
        checker: Checker = check_candidate,
        loader: Callable[[Path, str], list[Any]] | None = None,
    ) -> None:
        self.repo = repo
        self.workdir = workdir
        self.api = api
        self.run_id = run_id
        self.report = report
        self.publish = publish
        self.runner = runner
        self.prober = prober
        self.keeper = keeper
        self.checker = checker
        self.loader = loader
        self.statuses: dict[str, Status] = {}
        self._taken: dict[str, lane.Problem] = {}
        self._recorded: dict[str, str] = {}
        self._problems: queues.Queue[lane.Problem | None] = queues.Queue()
        self._ready: Ready | None = None
        self._recheck_pending: tuple[lane.Problem, threading.Event] | None = None
        self._recheck_result: State = "persists"
        self._recheck_seen = False
        self._lock = threading.Lock()
        self._safe_lock = threading.Lock()
        self._busy = False
        self._kept = 0
        self._pending = 0
        self._consecutive_unsolved = 0
        self._braked = False
        self.model_error: str | None = None
        self._closed = False
        self._settled = threading.Event()
        self._thread = threading.Thread(target=self._work, name="fixer", daemon=True)
        self._thread.start()

    # ----- the run's side -----------------------------------------------------

    def submit(self, problem: lane.Problem) -> bool:
        """Queue a problem once a run; False when it is known or the fixer paused.

        The problem's one failure-issue row is upserted here (signature
        `problem|<key>`), unless the caller already has one; an unsolved or
        fixed row is never handed over again.
        """
        with self._lock:
            if (self._closed or self._braked or self.model_error is not None
                    or problem.key in self.statuses):
                return False
        taken = self._with_issue(problem)
        if taken is None:
            return False
        problem = taken
        with self._lock:
            self.statuses[problem.key] = Status(problem.key, problem.question, problem.cause)
            self._taken[problem.key] = problem
        self._announce(problem.key, "queued", note=problem.note)
        with self._lock:
            if self._closed:
                return False
            self._pending += 1
            self._problems.put(problem)
        return True

    def at_safe_point(
        self,
        args: Callable[[], dict[str, Any]],
        recheck: Recheck | None = None,
    ) -> bool:
        """Serve a pending recheck on this thread, then try a waiting candidate
        on the live tab; True once one was kept and loaded."""
        with self._safe_lock:
            if recheck is not None:
                with self._lock:
                    self._recheck_seen = True
            with self._lock:
                pending, self._recheck_pending = self._recheck_pending, None
            if pending is not None:
                problem, asked = pending
                result: State = "persists"
                if recheck is not None:
                    try:
                        answer = recheck(problem)
                        if answer in {"persists", "answered", "gone"}:
                            result = answer
                    except Exception:
                        pass
                with self._lock:
                    self._recheck_result = result
                asked.set()
            ready = self._take_ready()
            if ready is not None:
                try:
                    self._announce(ready.problem.key, "probing", attempt=ready.attempt)
                    ready.probe = self.prober(ready, args())
                    if "file" in ready.probe:
                        ready.chosen = next(
                            (c for c in ready.candidates
                             if str(c.learned_file) == ready.probe["file"]),
                            ready.candidates[0],
                        )
                    with self._lock:
                        closed = self._closed
                    if ready.probe.get("ok") is True and not closed:
                        ready.kept = self._keep(ready)
                except Exception as error:
                    ready.probe = {"ok": False, "note": f"the probe/keep failed: {error}"}
                finally:
                    ready.done.set()
                if ready.kept is not None:
                    self._settled.wait(timeout=10)
        with self._lock:
            return self._kept > 0 and self._pending == 0

    def has_kept(self) -> bool:
        with self._lock:
            return self._kept > 0

    def consume_kept(self) -> None:
        with self._lock:
            self._kept = 0

    def close(self) -> None:
        """Stop after the current step; a fix under way goes back to open."""
        with self._lock:
            self._closed = True
            ready, self._ready = self._ready, None
            pending, self._recheck_pending = self._recheck_pending, None
            self._problems.put(None)
            unfinished = [problem for key, problem in self._taken.items()
                          if self._recorded.get(key) == "fixing"]
        if ready is not None:
            ready.done.set()
        if pending is not None:
            pending[1].set()
        for problem in unfinished:
            self._issue(problem, {
                "status": "open",
                "verification_notes": f"attempts {self._attempts_of(problem)}"
                                      f" of {MAX_ATTEMPTS}; the run ended before"
                                      " its fix finished",
            })

    def _with_issue(self, problem: lane.Problem) -> lane.Problem | None:
        """The problem's failure-issue row; None when it is unsolved or fixed."""
        from personal_os_worker.jobs.sites import failure_signature

        if problem.issue_id is not None:
            try:
                issue = self.api.autofill_failure(problem.issue_id) or {}
            except Exception:
                issue = {}
            return None if str(issue.get("status")) in {"unsolved", "fixed"} else problem
        signature = failure_signature(
            "generic", problem.cause, "problem|" + problem.key
        )
        try:
            issues = self.api.persist_autofill_step(
                problem.run_id or self.run_id,
                {"failures": [{
                    "signature": signature,
                    "category": problem.cause,
                    "question": problem.question,
                    "field_label": problem.question,
                    "field_fingerprint": "problem|" + problem.key,
                    "fact_key": problem.fact_key,
                    "error_message": problem.note,
                    "dom_signals": {},
                }]},
            )
        except Exception:
            return problem  # no row could be read: the fix still gets its try
        issue = next(
            (row for row in issues
             if str(row.get("signature") or "") == signature),
            issues[0] if issues else {},
        )
        if str(issue.get("status")) in {"unsolved", "fixed"}:
            return None
        issue_id = issue.get("id")
        return (
            dataclasses.replace(problem, issue_id=str(issue_id))
            if issue_id is not None else problem
        )
