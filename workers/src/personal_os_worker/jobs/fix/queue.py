"""The fix queue: one failure at a time in the background, kept at safe points.

The run hands a problem over (`submit`) and goes on. A background thread asks
the model for one file, checks it (policy, ruff, mypy, its examples, replay),
and waits. At its next safe point the run calls `at_safe_point`, which tries
the file on the live tab (`probe.py`), commits exactly it and its sanitized
fixtures (`keep.py`), and loads it into the running run — no reload.
"""

from __future__ import annotations

import dataclasses
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

from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.keep import (
    KeepError,
    Kept,
    fixture_rel,
    keep,
    learned_rel,
)
from personal_os_worker.jobs.fix.scratch import interpreter
from personal_os_worker.jobs.model_errors import ModelError
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.registry import policy

MAX_ATTEMPTS = 3
BRAKE = 3  # problems in a row that use every attempt without a keep
PROBE_TIMEOUT_SECONDS = 180
CHECK_TIMEOUT_SECONDS = 600


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
    """An attempt that passed its checks, waiting to be tried on the live tab."""

    problem: lane.Problem
    attempt_dir: Path
    learned_file: Path
    answer: lane.Answer
    attempt: int
    stem: str
    reuse: bool = False
    revised: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    probe: dict[str, Any] | None = None
    kept: Kept | None = None


Prober = Callable[[Ready, dict[str, Any]], dict[str, Any]]
Checker = Callable[[Path, Path, lane.Problem], str | None]
PersistCheck = Callable[[lane.Problem], bool]


def run_probe(ready: Ready, args: dict[str, Any]) -> dict[str, Any]:
    """The probe, in its own process, checking the candidate file over CDP."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(args | {"target": ready.problem.target}, handle, default=str)
        path = Path(handle.name)
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        process = subprocess.run(
            [
                str(interpreter()), "-m", "personal_os_worker.jobs.fix.probe",
                str(path), str(ready.learned_file), str(ready.problem.category),
            ],
            capture_output=True, text=True,
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


_ROOT = Path(__file__).resolve().parents[3]  # the workspace uv runs from


def _gate(command: list[str], name: str) -> str | None:
    """Run one gate command under the workspace; its failure text or None."""
    try:
        process = subprocess.run(
            command, cwd=_ROOT, capture_output=True, text=True,
            timeout=CHECK_TIMEOUT_SECONDS,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"{name} could not run: {error}"
    if process.returncode != 0:
        return f"{name} failed:\n{(process.stdout + process.stderr)[-3000:]}"
    return None


def check_candidate(attempt_dir: Path, learned_file: Path, problem: lane.Problem) -> str | None:
    """Every gate a candidate passes in one subprocess order; the first failure."""
    category, site = problem.category, problem.site
    violations = policy.check_file(learned_file, category=category, site=site)
    if violations:
        return "the policy refused it: " + "; ".join(violations)
    if "proven=True" in learned_file.read_text():
        return "a new or revised file stays on trial: proven must be False"
    for name, command in (
        ("ruff", ["uv", "run", "ruff", "check", str(learned_file)]),
        ("mypy", ["uv", "run", "mypy", str(learned_file)]),
        (
            "examples",
            ["uv", "run", "python", "-m", "personal_os_worker.jobs.fix.examples",
             str(learned_file), str(attempt_dir / "fixtures")],
        ),
    ):
        failed = _gate(command, name)
        if failed is not None:
            return failed
    if category in {"formats", "questions"}:
        return _gate(
            ["uv", "run", "python", "-m", "personal_os_worker.jobs.replay",
             "--json", "--learned", str(learned_file)],
            "replay",
        )
    return None


def _sites_arg(source: str, site: str) -> str | None:
    """The file with `site` added to its declaration's `sites=` tuple — one
    site per line with a trailing comma, indented under the keyword, so a
    growing tuple stays inside the line limit — or None when that tuple could
    not be edited safely."""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "sites" or not isinstance(keyword.value, ast.Tuple):
                continue
            value = keyword.value
            if not all(
                isinstance(item, ast.Constant) and isinstance(item.value, str)
                for item in value.elts
            ):
                return None
            names = [
                str(item.value) for item in value.elts
                if isinstance(item, ast.Constant)
            ]
            if site.casefold() in {name.casefold() for name in names}:
                return source  # already named
            if value.end_lineno is None or value.end_col_offset is None:
                return None
            indent = " " * (keyword.col_offset + 4)
            closing = " " * keyword.col_offset
            listing = "".join(f'{indent}"{name}",\n' for name in [*names, site])
            lines = source.splitlines(keepends=True)
            start_row, end_row = value.lineno - 1, value.end_lineno - 1
            lines[start_row:end_row + 1] = [
                lines[start_row][:value.col_offset]
                + f"(\n{listing}{closing})"
                + lines[end_row][value.end_col_offset:]
            ]
            return "".join(lines)
    return None


def _same_structure(problem: lane.Problem) -> list[Path]:
    """Learned files of this problem's type with its structure, not yet this site."""
    from personal_os_worker.jobs import registry

    category = problem.category
    if category == "routes":
        return []
    found: list[Path] = []
    for path in registry._learned_files(category):
        try:
            module = registry._import_file(path, category)
        except Exception:
            continue
        for value in vars(module).values():
            decl = registry._categories()[category]
            if not isinstance(value, decl):
                continue
            if problem.site.casefold() in registry._sites_of(value):
                continue  # already lists this site
            if category == "formats":
                if problem.fact_key and problem.fact_key in tuple(
                    getattr(value, "fact_keys", ())
                ):
                    found.append(path)
            elif str(getattr(value, "structure", "")) == problem.structure:
                found.append(path)
    return found


def add_site(source: str, site: str) -> str | None:
    """Public spelling of the sites-edit for callers and tests."""
    return _sites_arg(source, site)


def structure_of(source: str) -> str:
    """The `structure=` literal a source's declaration carries, or ""."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "structure" and isinstance(
                    keyword.value, ast.Constant
                ):
                    return str(keyword.value.value)
    return ""


def _declared_sites_and_fixtures(source: str) -> tuple[set[str], set[str]]:
    """One file's `sites=` strings (casefolded) and `Example(fixture=...)`
    strings, for comparing a revision with the file it revises."""
    import ast

    sites: set[str] = set()
    fixtures: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return sites, fixtures
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "sites" and isinstance(keyword.value, ast.Tuple):
                sites |= {
                    str(item.value).casefold()
                    for item in keyword.value.elts
                    if isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                }
            if (
                keyword.arg == "fixture"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                fixtures.add(keyword.value.value)
    return sites, fixtures


def _revision_dropped(old: str, new: str) -> str | None:
    """What a revision lost: an earlier site or an earlier example's fixture."""
    old_sites, old_fixtures = _declared_sites_and_fixtures(old)
    new_sites, new_fixtures = _declared_sites_and_fixtures(new)
    missing_sites = old_sites - new_sites
    if missing_sites:
        return (
            "a revision keeps every earlier site in sites=; yours dropped "
            + ", ".join(sorted(missing_sites))
        )
    missing_fixtures = old_fixtures - new_fixtures
    if missing_fixtures:
        return (
            "a revision keeps every earlier example; yours dropped the fixture "
            + ", ".join(sorted(missing_fixtures))
        )
    return None


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
        runner: lane.Runner = lane.run_claude,
        prober: Prober = run_probe,
        keeper: Callable[..., Kept] = keep,
        checker: Checker = check_candidate,
        problem_persists: PersistCheck | None = None,
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
        self.problem_persists = problem_persists
        self.loader = loader
        self.statuses: dict[str, Status] = {}
        self._taken: dict[str, lane.Problem] = {}
        self._recorded: dict[str, str] = {}
        self._problems: queues.Queue[lane.Problem | None] = queues.Queue()
        self._ready: Ready | None = None
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

    def at_safe_point(self, args: Callable[[], dict[str, Any]]) -> bool:
        """Try a waiting candidate on the live tab; True once it was kept and loaded."""
        with self._safe_lock:
            ready = self._take_ready()
            if ready is not None:
                try:
                    self._announce(ready.problem.key, "probing", attempt=ready.attempt)
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
        """Stop after the current step; a fix under way is recorded unsolved."""
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
                closed = (self._closed or self._braked
                          or self.model_error is not None)
                self._busy = True
                self._settled.clear()
            try:
                if closed:
                    self._finish(problem, "unsolved",
                                 "the fixer stopped before this problem")
                elif self.problem_persists is not None and not self.problem_persists(problem):
                    self._announce(problem.key, "kept",
                                   note="a kept fix already answers it")
                else:
                    self._fix(problem)
            except Exception as error:  # a fixer bug must never end the run
                self._finish(problem, "unsolved", f"the fixer failed: {str(error)[:200]}")
            finally:
                with self._lock:
                    self._busy = False
                    self._pending -= 1
                self._settled.set()

    def _attempt_dir(self, problem: lane.Problem, attempt: int) -> Path:
        return self.workdir / self.run_id / problem.slug / f"attempt-{attempt}"

    def _fix(self, problem: lane.Problem) -> None:
        attempts = self._attempts_of(problem)
        feedback: str | None = None
        same = [] if problem.category == "routes" else _same_structure(problem)
        revising = same[-1] if same else None
        if same and self._reuse(problem, same):
            return
        for attempt in range(attempts + 1, MAX_ATTEMPTS + 1):
            with self._lock:
                if self._closed:
                    self._finish(problem, "unsolved", "the run closed during the fix")
                    return
            self._announce(problem.key, "writing", attempt=attempt)
            self._record_attempt(problem, attempt)
            try:
                answer = lane.write(
                    problem, feedback, runner=self.runner,
                    revising=revising.read_text() if revising else None,
                    name=revising.stem if revising else "",
                )
            except (ModelError, OSError, subprocess.TimeoutExpired) as error:
                with self._lock:
                    self.model_error = str(error) or "the model is unavailable"
                self._finish(problem, "unsolved", self.model_error)
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
            if problem.category in {"kinds", "strategies", "questions"}:
                declared = structure_of(answer.file)
                if declared != problem.structure:
                    feedback = (
                        f"structure= must be exactly {problem.structure!r};"
                        f" yours was {declared!r}"
                    )
                    continue
            if revising is not None:
                dropped = _revision_dropped(revising.read_text(), answer.file)
                if dropped is not None:
                    feedback = dropped
                    continue
            feedback = self.checker(attempt_dir, learned_file, problem)
            if feedback is not None:
                continue
            ready = Ready(
                problem, attempt_dir, learned_file, answer, attempt,
                stem=stem, revised=revising is not None,
            )
            verdict = self._hand_over(ready)
            if verdict is None:
                return
            if verdict == "closed":
                self._finish(problem, "unsolved",
                             "the run closed during verification")
                return
            feedback = (
                "It passed the checks but failed on the live page: " + verdict
            )
        self._finish(problem, "unsolved",
                     f"{MAX_ATTEMPTS} attempts failed; last: {(feedback or '')[:300]}")

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
        """Each same-structure file missing this site gets the site added in a
        reuse attempt of its own, checked (policy, ruff — the proven flag and
        examples are already earned) and handed over like a model candidate:
        probed at the next safe point, kept on a readback, never edited in the
        live checkout. True once one is kept."""
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
            self._announce(problem.key, "testing", note=f"reuse {stem}")
            refused = self._reuse_check(learned_file, problem)
            if refused is not None:
                self._report(f"reuse of {stem} failed its checks: {refused}")
                continue
            answer = lane.Answer(
                "file", edited, f"reused {stem}", "", stem
            )
            ready = Ready(
                problem, attempt_dir, learned_file, answer, 0,
                stem=stem, reuse=True,
            )
            verdict = self._hand_over(ready)
            if verdict is None:
                return True
            if verdict == "closed":
                return False
            self._report(f"reuse of {stem} failed on the page: {verdict}")
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
        learned = learned_rel(problem.category, ready.stem)
        fixtures = fixture_rel(problem.category, ready.stem)
        if ready.reuse:
            subject = f"Reuse {ready.stem} for {problem.site}"
            summary = f"{problem.question!r} reads back with the same declaration."
        elif ready.revised:
            subject = f"Revise {ready.stem} for {problem.site}"
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
                self.repo, message, ready.attempt_dir, learned, fixtures,
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
