"""The fix queue: one failure at a time in the background, kept at the run's safe points.

The run hands a problem over (`submit`) and goes on. A background thread clones,
asks the model, and checks each attempt; an attempt that passed the gates and
the replay waits for the run. At its next safe point (between polls, after an
audit) the run calls `at_safe_point`, which tries the attempt on the live tab
(`probe.py`), keeps it when it reads back (`keep.py`), and records it. A kept
fix asks the run to reload with the new code once no other fix is mid-flight.
"""

from __future__ import annotations

import json
import os
import queue as queues
import subprocess
import tempfile
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from personal_os_worker.jobs import model
from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.keep import KeepError, Kept, keep
from personal_os_worker.jobs.fix.scratch import Scratch, interpreter, refusal
from personal_os_worker.jobs.model_errors import ModelError
from personal_os_worker.jobs.options import normalize_answer_question

MAX_ATTEMPTS = 3
MAX_PROBLEMS = 8
PROBE_TIMEOUT_SECONDS = 180


@dataclass
class Status:
    """Where one fix stands, for the terminal and the page."""

    key: str
    question: str
    cause: str
    state: str = "queued"  # writing, testing, probing, kept, unsolved, missing_fact
    attempt: int = 0
    note: str = ""

    def item(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Ready:
    """An attempt that passed its gates, waiting to be tried on the live tab."""

    problem: lane.Problem
    scratch: Scratch
    answer: lane.Answer
    check: lane.Check
    attempt: int
    diff: str = ""
    done: threading.Event = field(default_factory=threading.Event)
    probe: dict[str, Any] | None = None
    kept: Kept | None = None


Prober = Callable[[Ready, dict[str, Any]], dict[str, Any]]


def run_probe(ready: Ready, args: dict[str, Any]) -> dict[str, Any]:
    """The probe, in its own process, running the attempt's code."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(args | {"target": ready.problem.target}, handle, default=str)
        path = Path(handle.name)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ready.scratch.root / "workers/src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        process = subprocess.run(
            [str(interpreter(ready.scratch.repo)), "-m", "personal_os_worker.jobs.fix.probe",
             str(path)],
            cwd=ready.scratch.root / "workers", capture_output=True, text=True,
            timeout=PROBE_TIMEOUT_SECONDS, env=environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "note": f"the probe could not finish: {error}"}
    finally:
        path.unlink(missing_ok=True)
    if process.returncode != 0:
        return {"ok": False, "note": "the probe crashed: " + process.stderr.strip()[-500:]}
    lines = [line for line in process.stdout.splitlines() if line.strip().startswith("{")]
    try:
        result = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        return {"ok": False, "note": "the probe crashed: " + process.stderr.strip()[-500:]}
    if not isinstance(result, dict) or type(result.get("ok")) is not bool:
        return {"ok": False, "note": "no valid probe result"}
    return result


class FixQueue:
    def __init__(
        self,
        *,
        repo: Path,
        workdir: Path,
        api: Any,
        run_id: str,
        report: Callable[[str], None],
        publish: Callable[[list[dict[str, Any]]], None] | None = None,
        runner: lane.Runner = model.run,
        prober: Prober = run_probe,
        keeper: Callable[[Path, str, str], Kept] = keep,
        checker: Callable[[Scratch, lane.Problem], lane.Check] = lane.check,
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
        self.statuses: dict[str, Status] = {}
        # What each problem's failure issue was last recorded as, by problem key.
        self._taken: dict[str, lane.Problem] = {}
        self._recorded: dict[str, str] = {}
        self._problems: queues.Queue[lane.Problem | None] = queues.Queue()
        self._ready: Ready | None = None
        self._lock = threading.Lock()
        self._safe_lock = threading.Lock()
        self._busy = False
        self._kept = 0
        self._pending = 0
        self.model_error: str | None = None
        self._closed = False
        self._settled = threading.Event()
        self._thread = threading.Thread(target=self._work, name="fixer", daemon=True)
        self._thread.start()

    # ----- the run's side -----------------------------------------------------

    def submit(self, problem: lane.Problem) -> bool:
        """Queue a problem once a run; False when it is known or the run's fixes are spent."""
        with self._lock:
            if self._closed or self.model_error is not None or problem.key in self.statuses:
                return False
            if len(self.statuses) >= MAX_PROBLEMS:
                return False
            self.statuses[problem.key] = Status(problem.key, problem.question, problem.cause)
            self._taken[problem.key] = problem
        self._announce(problem.key, "queued", note=problem.note)
        with self._lock:
            if self._closed:
                return False
            self._pending += 1
            self._problems.put(problem)
        return True

    def at_safe_point(self, args: Callable[[], dict[str, Any]]) -> bool:
        """Try a waiting attempt on the live tab; True once the run should reload.

        `args` builds what the probe needs (the browser, the tab, the run's
        knowledge) from the run as it is now.
        """
        with self._safe_lock:
            ready = self._take_ready()
            if ready is not None:
                try:
                    self._announce(ready.problem.key, "probing", attempt=ready.attempt)
                    if (refusal(ready.scratch.changes()) is not None
                            or ready.scratch.diff() != ready.diff):
                        raise KeepError("the fix changed after verification")
                    ready.probe = self.prober(ready, args())
                    with self._lock:
                        closed = self._closed
                    if ready.probe.get("ok") is True and not closed:
                        ready.kept = self._keep(ready)
                except Exception as error:
                    ready.probe = {"ok": False, "note": f"the probe/keep failed: {error}"}
                finally:
                    ready.done.set()
                if ready.kept is not None:
                    # Its thread only tidies up now; let it, so the answer is current.
                    self._settled.wait(timeout=10)
        with self._lock:
            return self._kept > 0 and self._pending == 0

    def has_kept(self) -> bool:
        with self._lock:
            return self._kept > 0

    def consume_kept(self) -> None:
        """The kept fixes already did their work here; they need no reload."""
        with self._lock:
            self._kept = 0

    def close(self) -> None:
        """Stop after the current step; an attempt waiting for the run is let go.

        The background thread dies with the process, so a fix still under way is
        recorded unsolved here, on the run's side, while the API is open:
        otherwise its issue would stay `fixing`, which nothing ever reopens.
        """
        with self._lock:
            self._closed = True
            ready, self._ready = self._ready, None
            self._problems.put(None)
            unfinished = [problem for key, problem in self._taken.items()
                          if self._recorded.get(key) == "fixing"]
        if ready is not None:
            ready.done.set()
        for problem in unfinished:
            self._issue(problem, {"status": "unsolved",
                                  "verification_notes": "the run ended before its fix finished"})

    # ----- the background thread ----------------------------------------------

    def _take_ready(self) -> Ready | None:
        with self._lock:
            if self._closed:
                return None
            ready, self._ready = self._ready, None
            return ready

    def _work(self) -> None:
        while True:
            problem = self._problems.get()
            if problem is None:
                return
            with self._lock:
                closed = self._closed or self.model_error is not None
                self._busy = True
                self._settled.clear()
            try:
                if closed:
                    self._finish(problem, "unsolved", "the fixer stopped before this problem")
                else:
                    self._fix(problem)
            except Exception as error:  # a fixer bug must never end the run
                self._finish(problem, "unsolved", f"the fixer failed: {str(error)[:200]}")
            finally:
                with self._lock:
                    self._busy = False
                    self._pending -= 1
                self._settled.set()

    def _fix(self, problem: lane.Problem) -> None:
        self._issue(problem, {"status": "fixing"})
        scratch = Scratch.create(self.repo, self.workdir / problem.slug)
        try:
            fixtures = lane.write_fixtures(scratch, problem)
            feedback: str | None = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                with self._lock:
                    closed = self._closed
                if closed:
                    self._finish(problem, "unsolved", "the run closed during the fix")
                    return
                self._announce(problem.key, "writing", attempt=attempt)
                try:
                    answer = lane.write(scratch, problem, fixtures, feedback, runner=self.runner)
                except (ModelError, OSError, subprocess.TimeoutExpired) as error:
                    with self._lock:
                        self.model_error = str(error) or "the model is unavailable"
                    self._finish(problem, "unsolved", self.model_error)
                    return
                with self._lock:
                    closed = self._closed
                if closed:
                    self._finish(problem, "unsolved", "the run closed during the fix")
                    return
                if answer is None:
                    feedback = "Your answer was not the JSON asked for. Answer as instructed."
                    continue
                if answer.kind == "missing_fact":
                    self._finish(problem, "missing_fact",
                                 f"needs a fact: {answer.missing_fact or answer.summary}")
                    return
                if answer.kind == "impossible":
                    self._finish(problem, "unsolved", f"no code fix: {answer.summary}")
                    return
                self._announce(problem.key, "testing", attempt=attempt, note=answer.summary)
                checked = self.checker(scratch, problem)
                if not checked.passed:
                    feedback = checked.feedback
                    continue
                ready = Ready(problem, scratch, answer, checked, attempt, diff=scratch.diff())
                with self._lock:
                    closed = self._closed
                    if not closed:
                        self._ready = ready
                if closed:
                    self._finish(problem, "unsolved", "the run closed during verification")
                    return
                self._announce(problem.key, "waiting", attempt=attempt,
                               note="tested; tried on the page at the next pause")
                ready.done.wait()
                if ready.kept is not None:
                    return
                with self._lock:
                    closed = self._closed
                if closed:
                    self._finish(problem, "unsolved", "the run closed before the fix was kept")
                    return
                feedback = (
                    "It passed the tests but failed on the live page: "
                    + str((ready.probe or {}).get("note") or "no readback")
                )
            self._finish(problem, "unsolved",
                         f"{MAX_ATTEMPTS} attempts failed; last: {(feedback or '')[:300]}")
        finally:
            scratch.remove()

    # ----- keeping and recording ------------------------------------------------

    def _keep(self, ready: Ready) -> Kept | None:
        problem = ready.problem
        diff = ready.scratch.diff()
        changes = ready.scratch.changes()
        if refusal(changes) is not None or diff != ready.diff:
            raise KeepError("the fix changed after verification or during the live probe")
        files = [change.path for change in changes]
        message = (
            f"Fix {problem.cause.replace('_', ' ')} for {problem.question!r}\n\n"
            f"{ready.answer.summary}\n\n"
            f"Written and kept during run {problem.run_id or self.run_id}: tests, replay, "
            f"and the live control passed ({(ready.probe or {}).get('note')})."
        )
        try:
            kept = self.keeper(self.repo, diff, message)
        except KeepError as error:
            ready.probe = {"ok": False, "note": f"it could not be kept: {error}"}
            return None
        # Once committed, metadata/UI failures must not turn this into another
        # attempt or leave the background thread believing it was not kept.
        ready.kept = kept
        with self._lock:
            self._kept += 1
        replay = next((gate for gate in ready.check.gates if gate.name == "replay"), None)
        verification = _verification(replay.output if replay else "")
        try:
            self.api.record_code_change(
                kept.commit, origin="worker", parent=kept.parent,
                failure={"run_id": problem.run_id or self.run_id,
                         "question_key": normalize_answer_question(problem.question) or None,
                         "issue_id": problem.issue_id},
                verification=verification,
                detail={"cause": problem.cause, "question": problem.question,
                        "summary": ready.answer.summary, "attempts": ready.attempt,
                        "files": files,
                        "tests": [gate.name for gate in ready.check.gates if gate.passed],
                        "probe": ready.probe, "pushed": kept.pushed, "diff": diff[:100_000]},
            )
        except Exception as error:
            self._report(f"kept {kept.commit[:7]}, but recording it failed: {error}")
        self._issue(problem, {"status": "fixed", "fixed_commit": kept.commit,
                              "diagnosis": ready.answer.summary,
                              "proposed_patch": diff[:200_000],
                              "verification_notes": f"attempt {ready.attempt}: "
                              f"{(ready.probe or {}).get('note')}"
                              + ("" if kept.pushed else f"; {kept.note}")})
        self._announce(problem.key, "kept", attempt=ready.attempt,
                       note=f"{kept.commit[:7]} {ready.answer.summary}"
                       + ("" if kept.pushed else " (not pushed)"))
        return kept

    def _finish(self, problem: lane.Problem, state: str, note: str) -> None:
        # Recorded before it is shown, so what the page says is never ahead of the issue.
        self._issue(problem, {"status": "unsolved", "verification_notes": note[:30_000]})
        self._announce(problem.key, state, note=note)

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
        # A disconnected terminal must not strand a committed fix.
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


def _verification(output: str) -> dict[str, int] | None:
    """The replay's counts, from the JSON line `os hunt replay --json` ends with."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                return {"sampled": int(data["sampled"]), "failed": int(data["failed"])}
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                return None
    return None
