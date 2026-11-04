"""The background thread: one problem at a time, from recheck and reuse to the last attempt."""

from __future__ import annotations

import subprocess
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.base import (
    MAX_ATTEMPTS,
    Candidate,
    FixBase,
    Ready,
    State,
)
from personal_os_worker.jobs.fix.gates import _gate
from personal_os_worker.jobs.fix.keep import fixture_rel
from personal_os_worker.jobs.fix.source import (
    _kind_candidates,
    _revision_dropped,
    _same_structure,
    _sites_arg,
    structure_of,
)
from personal_os_worker.jobs.model_errors import ModelError
from personal_os_worker.jobs.registry import policy


class BackgroundMixin(FixBase):
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
                self._queued.discard(problem.key)
                self._working = problem.key
                directed = bool(self._directions.get(problem.key))
                closed = (self._closed or (self._braked and not directed)
                          or self.model_error is not None)
                self._busy = True
                self._settled.clear()
            try:
                if closed:
                    self._leave_open(problem,
                                     "the fixer stopped before this problem")
                else:
                    self._fix(problem)
            except Exception as error:  # a fixer bug must never end the run
                self._finish(problem, "unsolved", f"the fixer failed: {str(error)[:200]}")
            finally:
                with self._lock:
                    self._busy = False
                    self._working = None
                    self._pending -= 1
                    # A direction that came too late for the last call still gets one.
                    said = len(self._directions.get(problem.key, ()))
                    if (said > self._consumed.get(problem.key, 0) and not self._closed
                            and self.model_error is None):
                        self._queued.add(problem.key)
                        self._pending += 1
                        self._problems.put(problem)
                self._settled.set()

    def _attempt_dir(self, problem: lane.Problem, attempt: int) -> Path:
        return self.workdir / self.run_id / problem.slug / f"attempt-{attempt}"

    def _ask_recheck(self, problem: lane.Problem) -> State | str:
        """Ask the run thread to re-check this problem on the page; waits on its
        event. Returns the state, or "closed" when the run ended first."""
        with self._lock:
            # No run ever asked for a recheck: the safe point cannot serve one.
            if self._closed or not self._recheck_seen:
                return "closed" if self._closed else "persists"
        done = threading.Event()
        with self._lock:
            self._recheck_pending = (problem, done)
        done.wait()
        with self._lock:
            if self._closed:
                return "closed"
            return self._recheck_result

    def _answered(self, problem: lane.Problem) -> None:
        self._issue(problem, {"status": "fixed",
                              "verification_notes": "a kept fix already answers it"})
        self._announce(problem.key, "kept",
                       note="a kept fix already answers it")
        with self._lock:
            self._consecutive_unsolved = 0

    def _gone(self, problem: lane.Problem) -> None:
        self._issue(problem, {
            "status": "open",
            "verification_notes": f"attempts {self._attempts_of(problem)} of {MAX_ATTEMPTS}",
        })
        self._announce(problem.key, "later",
                       note="no longer on the page; tried again when next seen")

    def _leave_open(self, problem: lane.Problem, note: str) -> None:
        """Never-attempted stays open: only three real attempts make unsolved."""
        self._issue(problem, {"status": "open"})
        self._announce(problem.key, "later", note=note)

    def _recheck_or_done(self, problem: lane.Problem) -> bool:
        """True when a recheck settled the problem (answered or gone)."""
        state = self._ask_recheck(problem)
        if state == "answered":
            self._answered(problem)
            return True
        if state == "gone":
            self._gone(problem)
            return True
        if state == "closed":
            self._leave_open(problem, "the run closed during the fix")
            return True
        return False

    def _said(self, key: str) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._directions.get(key, ()))

    def _fix(self, problem: lane.Problem) -> None:
        attempt = self._attempts_of(problem)
        feedback: str | None = None
        if problem.category == "kinds":
            same: list[Path] = _kind_candidates(problem)
            revising: Path | None = None
        elif problem.category == "routes":
            same, revising = [], None
        else:
            same = _same_structure(problem)
            revising = same[-1] if same else None
        if self._recheck_or_done(problem):
            return
        # A direction is why this run is here again: it wants a new file, not a reuse.
        if same and not self._said(problem.key) and self._reuse(problem, same):
            return
        limit, seen, first = MAX_ATTEMPTS, 0, True
        while True:
            with self._lock:
                if self._closed:
                    self._leave_open(problem, "the run closed during the fix")
                    return
            said = len(self._said(problem.key))
            if said > seen:  # each direction gives the problem three more attempts
                limit, seen = attempt + MAX_ATTEMPTS, said
            if attempt >= limit:
                break
            if not first and self._recheck_or_done(problem):
                return
            first = False
            attempt += 1
            settled, feedback = self._attempt(problem, attempt, feedback, revising)
            if settled:
                return
        self._finish(problem, "unsolved",
                     f"{limit} attempts failed; last: {(feedback or '')[:300]}")

    def _attempt(
        self, problem: lane.Problem, attempt: int, feedback: str | None, revising: Path | None
    ) -> tuple[bool, str | None]:
        """One model call and what follows it: whether that settled the problem, and
        the feedback for the next attempt."""
        directions = self._said(problem.key)
        with self._lock:
            self._consumed[problem.key] = len(directions)
        self._announce(problem.key, "writing", attempt=attempt)
        self._record_attempt(problem, attempt)
        try:
            answer = lane.write(
                problem, feedback, runner=self.runner,
                revising=revising.read_text() if revising else None,
                name=revising.stem if revising else "",
                directions=directions,
            )
        except (ModelError, OSError, subprocess.TimeoutExpired) as error:
            with self._lock:
                self.model_error = str(error) or "the model is unavailable"
            self._leave_open(problem, self.model_error)
            return True, None
        if answer is None:
            return False, "Your answer was not the JSON asked for. Answer as instructed."
        if answer.kind != "file" and len(self._said(problem.key)) > len(directions):
            return False, f"You answered {answer.kind}: {answer.summary} A new direction has come."
        if answer.kind == "missing_fact":
            self._finish(problem, "missing_fact",
                         f"needs a fact: {answer.missing_fact or answer.summary}")
            return True, None
        if answer.kind == "closed" and problem.cause == "posting_not_found":
            return self._closed_answer(problem, answer, directions)
        if answer.kind != "file":
            self._finish(problem, "unsolved", f"no code fix: {answer.summary}")
            return True, None
        self._announce(problem.key, "testing", attempt=attempt, note=answer.summary)
        attempt_dir, learned_file, stem = self._write_attempt(problem, attempt, answer, revising)
        refused = self._check_attempt(problem, answer, revising, attempt_dir, learned_file)
        if refused is not None:
            return False, refused
        ready = Ready(
            problem, answer, attempt,
            [Candidate(attempt_dir, learned_file, stem)],
            revised=revising is not None,
        )
        verdict = self._hand_over(ready)
        if verdict is None:
            return True, None
        if verdict == "closed":
            self._leave_open(problem, "the run closed during verification")
            return True, None
        if verdict == "gone":
            self._gone(problem)
            return True, None
        return False, "It passed the checks but failed on the live page: " + verdict

    def _closed_answer(
        self, problem: lane.Problem, answer: lane.Answer, directions: tuple[str, ...]
    ) -> tuple[bool, str | None]:
        """A posting the model calls gone, on the page's words and your word or the
        site's own: the words must be on the page, and must be a closed notice unless
        you directed this. A row never leaves the board on the model's reading alone."""
        from personal_os_worker.jobs.posting_page import posting_closed

        def plain(text: str) -> str:
            return " ".join(text.split()).casefold()

        page = plain(f"{problem.evidence.get('arrival_text') or ''}\n"
                     f"{problem.evidence.get('page_text') or ''}")
        if not answer.quote or plain(answer.quote) not in page:
            return False, f"Your quote {answer.quote!r} is not on the page; quote it exactly."
        if not directions and not posting_closed(answer.quote):
            self._finish(problem, "unsolved",
                         f"reads as closed ({answer.summary}), but the page says only "
                         f"{answer.quote!r}; a direction from you can settle it")
            return True, None
        # No commit fixed this, and a row only reaches archived through unsolved.
        notes = f"the posting is gone: {answer.quote}"
        self._issue(problem, {"status": "unsolved", "verification_notes": notes})
        self._issue(problem, {"status": "archived", "verification_notes": notes})
        with self._lock:
            self._closed_quotes[problem.key] = answer.quote
            self._consecutive_unsolved = 0
        self._announce(problem.key, "closed", note=answer.summary)
        return True, None

    def _write_attempt(
        self, problem: lane.Problem, attempt: int, answer: lane.Answer, revising: Path | None
    ) -> tuple[Path, Path, str]:
        """Write one attempt's file and fixtures; its folder, file, and stem."""
        stem = revising.stem if revising else self._file_name(problem, answer)
        attempt_dir = self._attempt_dir(problem, attempt)
        learned_dir = attempt_dir / problem.category / "learned"
        learned_dir.mkdir(parents=True, exist_ok=True)
        learned_file = learned_dir / f"{stem}.py"
        learned_file.write_text(answer.file)
        fixtures_dir = attempt_dir / "fixtures"
        if revising is not None:
            self._committed_fixtures(problem.category, stem, fixtures_dir)
        self._write_fixtures(problem, fixtures_dir)
        return attempt_dir, learned_file, stem

    def _check_attempt(
        self,
        problem: lane.Problem,
        answer: lane.Answer,
        revising: Path | None,
        attempt_dir: Path,
        learned_file: Path,
    ) -> str | None:
        """The first check a written attempt fails, as feedback for the next one, or None."""
        if problem.category in {"kinds", "strategies", "questions"}:
            declared = structure_of(answer.file)
            if declared != problem.structure:
                return (
                    f"structure= must be exactly {problem.structure!r};"
                    f" yours was {declared!r}"
                )
        if revising is not None:
            dropped = _revision_dropped(revising.read_text(), answer.file)
            if dropped is not None:
                return dropped
        return self.checker(attempt_dir, learned_file, problem)

    def _hand_over(self, ready: Ready) -> str | None:
        """Park a checked candidate for the run's next safe point and wait for
        the verdict; None once it is kept, "closed" when the run ended, else
        the probe's note."""
        with self._lock:
            closed = self._closed
            if not closed:
                self._ready = ready
        if closed:
            return "closed"
        self._announce(ready.problem.key, "waiting", attempt=ready.attempt,
                       note="tested; tried on the page at the next pause")
        ready.done.wait()
        if ready.kept is not None:
            with self._lock:
                self._consecutive_unsolved = 0
            return None
        if (ready.probe or {}).get("gone") is True:
            return "gone"
        with self._lock:
            if self._closed:
                return "closed"
        return str((ready.probe or {}).get("note") or "no readback")

    def _file_name(self, problem: lane.Problem, answer: lane.Answer) -> str:
        """The file's name: site and goal for a route, the answer's readable
        name otherwise; a stem already taken gets a numeric suffix, so a new
        file never overwrites another. Only a revision writes over its own
        stem, and routes keep their site and goal."""
        base = lane.name_of(problem, answer)
        if problem.category == "routes":
            return base
        try:
            taken = {
                path.stem
                for path in self._learned_dir(problem.category).glob("*.py")
            }
        except Exception:
            return base
        name, index = base, 2
        while name in taken:
            name, index = f"{base}_{index}", index + 1
        return name

    def _learned_dir(self, category: str) -> Path:
        from personal_os_worker.jobs import registry

        return Path(registry._learned_package(category).__path__[0])

    def _write_fixtures(self, problem: lane.Problem, fixtures: Path) -> None:
        """This problem's page files, site-named, .html/.json only: a
        screenshot is a picture of the filled form, never a fixture."""
        from personal_os_worker.jobs.fix.sanitize import sanitize_evidence

        fixtures.mkdir(parents=True, exist_ok=True)
        files = problem.page_files
        with suppress(Exception):
            # A finding may arrive with raw evidence; every fact text still
            # leaves before it becomes a committed fixture.
            files = sanitize_evidence(files, self._facts())
        for name, content in sorted(files.items()):
            if name.endswith((".html", ".json")):
                target = fixtures / lane.fixture_name(problem.site, name)
                target.write_bytes(content)

    def _committed_fixtures(self, category: str, stem: str, fixtures: Path) -> None:
        """Every fixture the file being revised or reused already has, copied
        into the attempt so its earlier examples still find theirs."""
        committed = self.repo / fixture_rel(category, stem)
        if not committed.is_dir():
            return
        fixtures.mkdir(parents=True, exist_ok=True)
        for file in committed.iterdir():
            if file.is_file():
                (fixtures / file.name).write_bytes(file.read_bytes())

    def _facts(self) -> dict[str, Any]:
        try:
            return {str(row.get("key")): row.get("value")
                    for row in self.api.facts() or [] if isinstance(row, dict)}
        except Exception:
            return {}

    def _reuse(self, problem: lane.Problem, same: list[Path]) -> bool:
        """Each candidate file missing this site gets the site added in a reuse
        attempt of its own and gated (policy, ruff — the proven flag and
        examples are already earned); the survivors go over as ONE Ready, so a
        single safe point settles the whole reuse step. True when the problem
        is settled — kept, gone, or the run closed."""
        candidates: list[Candidate] = []
        for path in same:
            stem = path.stem
            attempt_dir = (
                self.workdir / self.run_id / problem.slug / f"reuse-{stem}"
            )
            try:
                edited = _sites_arg(path.read_text(), problem.site)
            except OSError:
                continue
            if edited is None:
                continue
            learned_dir = attempt_dir / problem.category / "learned"
            learned_dir.mkdir(parents=True, exist_ok=True)
            learned_file = learned_dir / f"{stem}.py"
            learned_file.write_text(edited)
            fixtures_dir = attempt_dir / "fixtures"
            self._committed_fixtures(problem.category, stem, fixtures_dir)
            self._write_fixtures(problem, fixtures_dir)
            refused = self._reuse_check(learned_file, problem)
            if refused is not None:
                self._report(f"reuse of {stem} failed its checks: {refused}")
                continue
            candidates.append(Candidate(attempt_dir, learned_file, stem))
        if not candidates:
            return False
        self._announce(problem.key, "testing",
                       note=f"reuse {', '.join(c.stem for c in candidates)}")
        answer = lane.Answer("file", "", "reused an existing file", "", "")
        ready = Ready(problem, answer, 0, candidates, reuse=True)
        verdict = self._hand_over(ready)
        if verdict is None:
            return True
        if verdict == "gone":
            self._gone(problem)
            return True
        if verdict == "closed":
            self._leave_open(problem, "the run closed during reuse")
            return True
        self._report(f"reuse failed on the page: {verdict}")
        return False

    def _reuse_check(self, learned_file: Path, problem: lane.Problem) -> str | None:
        """A reuse's whole gate: the policy for this site, then ruff. Only a
        string literal changed, so no mypy or examples — and the file keeps
        whatever proven flag it earned."""
        violations = policy.check_file(
            learned_file, category=problem.category, site=problem.site
        )
        if violations:
            return "the policy refused it: " + "; ".join(violations)
        return _gate(
            ["uv", "run", "ruff", "check", str(learned_file)], "ruff"
        )
