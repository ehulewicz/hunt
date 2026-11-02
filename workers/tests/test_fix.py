"""The fixer: a file is drafted, checked, tried live, kept, and loaded."""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from personal_os_worker.jobs import registry
from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.keep import Kept, keep
from personal_os_worker.jobs.fix.queue import MAX_ATTEMPTS, FixQueue, _same_structure
from personal_os_worker.jobs.fix.trial import Trial
from personal_os_worker.jobs.registry import policy
from personal_os_worker.jobs.registry.api import Strategy as api_Strategy

JOBS = "workers/src/personal_os_worker/jobs"

KIND_SOURCE = '''from personal_os_worker.jobs.registry.api import Example, Kind

KIND = Kind(
    name="odd_widget",
    selector="[data-widget=odd]",
    reads="value",
    strategies=(),
    structure="odd_widget",
    sites=("fixture.example",),
    proven=False,
    examples=(Example(fixture="page.html", selector="[data-widget=odd]", value="",
                      expected="3"),),
)
'''


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout of `main` with a remote, as the device's checkout is."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    git(work, "config", "user.name", "test")
    git(work, "config", "user.email", "test@example.com")
    (work / "README").write_text("x")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "start")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "origin", "main")
    return work


# ----- the policy: escapes are refused before import --------------------------

@pytest.mark.parametrize(
    "source",
    [
        "import re\nx = re.enum\nKIND = Kind(name='k', selector='s',"
        " reads='text', strategies=(), site='a.com')",
        "def g():\n    yield 1\nx = [f for f in g()].__iter__\n",
        "x = '{a}'.format_map({'a': 1})\n",
        "x = api.something_private\n",
    ],
)
def test_the_policy_refuses_global_and_format_escapes(source: str) -> None:
    header = "from personal_os_worker.jobs.registry.api import Kind\n"
    violations = policy.check_source(header + source, category="kinds")
    assert violations, source


def test_the_policy_refuses_an_engine_prefixed_selector_and_a_quoted_reads() -> None:
    from personal_os_worker.jobs.registry.kinds import _reads_script, _valid_selector

    assert not _valid_selector("xpath=//div")
    assert not _valid_selector("text=hello")
    assert not _valid_selector("internal:control=checkbox")
    assert not _valid_selector("css=div >> input")
    assert _valid_selector("[data-widget=odd]")
    assert _reads_script("attribute:aria-valuenow' OR 1") == ""
    assert _reads_script("text;alert(1)") == ""
    assert _reads_script("attribute:data-x")


# ----- a kept fix: the file and its fixtures, on main ---------------------------

def _attempt_dir(tmp: Path, file_source: str = KIND_SOURCE, fixtures: bool = True) -> Path:
    attempt = tmp / "attempt-1"
    learned = attempt / "kinds/learned"
    learned.mkdir(parents=True)
    (learned / "fixture_example_x.py").write_text(file_source)
    if fixtures:
        (attempt / "fixtures").mkdir()
        (attempt / "fixtures/page.html").write_text("<div data-widget='odd'>3</div>")
    return attempt


def test_keep_commits_exactly_the_learned_file_and_fixtures(repo: Path, tmp_path: Path) -> None:
    attempt = _attempt_dir(tmp_path)
    learned_rel = f"{JOBS}/registry/kinds/learned/fixture_example_x.py"
    fixture_rel = "workers/tests/fixtures/learned/kinds/fixture_example_x"
    kept = keep(repo, "a fix", attempt, learned_rel, fixture_rel)
    assert (repo / learned_rel).read_text() == KIND_SOURCE
    assert (repo / fixture_rel / "page.html").exists()
    assert kept.pushed
    show = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert set(show) == {learned_rel, f"{fixture_rel}/page.html"}
    # main moved on the remote too
    assert git(repo, "ls-remote", "origin", "main").split()[0] == kept.commit


def test_a_refused_push_keeps_the_commit_and_says_so(repo: Path, tmp_path: Path) -> None:
    git(repo, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    kept = keep(repo, "a fix", _attempt_dir(tmp_path),
                f"{JOBS}/registry/kinds/learned/x.py",
                "workers/tests/fixtures/learned/kinds/x")
    assert not kept.pushed and "not pushed" in kept.note
    assert git(repo, "rev-parse", "HEAD").strip() == kept.commit


# ----- the queue: attempts, brake, problem rows ----------------------------------

class Api:
    def __init__(self, issues: dict[str, dict[str, Any]] | None = None) -> None:
        self.issues = issues or {}
        self.next_id = 0
        self.updates: list[dict[str, Any]] = []

    def autofill_failure(self, issue_id: str) -> dict[str, Any]:
        return self.issues.get(issue_id) or {}

    def update_autofill_failure(self, issue_id: str, changes: dict[str, Any]) -> None:
        self.updates.append({"id": issue_id, **changes})
        self.issues.setdefault(issue_id, {}).update(changes)

    def record_code_change(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    def close(self) -> None:
        pass





def _queue(api: Api, tmp_path: Path, **kwargs: Any) -> FixQueue:
    return FixQueue(
        repo=tmp_path, workdir=tmp_path / "fix", api=api, run_id="run-1",
        report=lambda _line: None, publish=kwargs.pop("publish", None),
        runner=kwargs.pop("runner", None) or (lambda _c, timeout=0: "{}"),
        prober=kwargs.pop("prober", None) or (lambda _r, _a: {"ok": False, "note": "no"}),
        checker=kwargs.pop("checker", None)
        or (lambda _d, _f, _p: "policy refused it"),
        keeper=kwargs.pop("keeper", None) or (lambda *a, **k: Kept("c", "p", True)),
        **kwargs,
    )


def _settle(queue: FixQueue, count: int = 1) -> None:
    for _ in range(400):
        if queue._pending <= 0:
            return
        import time
        time.sleep(0.01)


def test_a_bad_answer_counts_an_attempt_and_three_attempts_is_all(
    tmp_path: Path,
) -> None:
    api = Api({"i1": {"status": "open", "verification_notes": ""}})
    lines: list[str] = []
    queue = FixQueue(
        repo=tmp_path, workdir=tmp_path / "fix", api=api, run_id="run-1",
        report=lines.append, runner=lambda _c, timeout=0: "not json",
        prober=lambda _r, _a: {"ok": False, "note": "no"},
        checker=lambda _d, _f, _p: None,
    )
    try:
        key = _problem().key
        queue.submit(_problem())
        for _ in range(600):
            if queue.statuses[key].state == "unsolved":
                break
            import time
            time.sleep(0.01)
        assert queue.statuses[key].state == "unsolved"
        notes = [str(u.get("verification_notes") or "") for u in api.updates]
        assert any(note.startswith(f"attempts {MAX_ATTEMPTS} of") for note in notes)
    finally:
        queue.close()


def test_attempts_carry_across_queues_through_the_issue_note(tmp_path: Path) -> None:
    api = Api({"i1": {"status": "open", "verification_notes": f"attempts 2 of {MAX_ATTEMPTS}"}})
    calls: list[int] = []

    def runner(command: Any, timeout: float = 0) -> str:
        calls.append(1)
        return "{}"

    queue = _queue(api, tmp_path, runner=runner)
    try:
        queue.submit(_problem())
        for _ in range(400):
            if queue.statuses[_problem().key].state == "unsolved":
                break
            import time
            time.sleep(0.01)
        assert queue.statuses[_problem().key].state == "unsolved"
        assert len(calls) == 1  # only the third attempt remained
    finally:
        queue.close()


def test_three_unsolved_in_a_row_brakes_the_fixer(tmp_path: Path) -> None:
    api = Api({"i1": {"status": "open"}, "i2": {"status": "open"},
               "i3": {"status": "open"}, "i4": {"status": "open"}})
    published: list[dict[str, Any]] = []
    lines: list[str] = []
    queue = FixQueue(
        repo=tmp_path, workdir=tmp_path / "fix", api=api, run_id="run-1",
        report=lines.append, publish=lambda items: published.extend(items),
        runner=lambda _c, timeout=0: json.dumps(
            {"kind": "file", "file": "x = 1\n", "missing_fact": "", "summary": "s"}
        ),
        prober=lambda _r, _a: {"ok": False, "note": "no"},
        checker=lambda _d, _f, _p: "policy refused it",
    )
    try:
        problems = [
            _problem(i, issue_id=f"i{i}") for i in range(1, 5)
        ]
        for problem in problems:
            queue.submit(problem)
        for _ in range(800):
            if queue._braked:
                break
            import time
            time.sleep(0.01)
        assert queue._braked
        assert any("paused" in line for line in lines)
        assert any(item.get("key") == "fixer" and item.get("state") == "paused"
                   for item in published)
        # the fourth problem is finished, but spent no attempt
        assert not any(
            "attempts " in str(u.get("verification_notes") or "")
            for u in api.updates if u.get("id") == "i4"
        )
    finally:
        queue.close()


def test_a_fixed_or_unsolved_problem_is_never_resubmitted(tmp_path: Path) -> None:
    api = Api({})
    queue = _queue(api, tmp_path)
    try:
        assert queue.submit(_problem()) is True
        assert queue.submit(_problem()) is False  # same key, once a run
    finally:
        queue.close()


# ----- api.VERSION: a new fixer reopens unsolved problems once -----------------

def test_reopen_for_version_reopens_unsolved_problems_once(tmp_path: Path) -> None:
    from personal_os_worker.jobs import registry

    class VersionApi(Api):
        def autofill_failures(self, status: str = "") -> list[dict[str, Any]]:
            return [
                issue for issue in self.issues.values()
                if not status or issue.get("status") == status
            ]

    api = VersionApi({
        "a": {"id": "a", "status": "unsolved", "signature": "generic|wall|problem|x"},
        "b": {"id": "b", "status": "unsolved", "signature": "generic|wall|other"},
        "c": {"id": "c", "status": "fixed", "signature": "generic|wall|problem|y"},
    })
    state = tmp_path / "state"
    state.mkdir()
    assert registry.reopen_for_version(state, api) == 1
    assert api.issues["a"]["status"] == "open"
    assert api.issues["b"]["status"] == "unsolved"  # not a problem row
    assert api.issues["c"]["status"] == "fixed"
    # written marker: a second call in the same version reopens nothing
    assert registry.reopen_for_version(state, api) == 0
    assert api.issues["a"]["status"] == "open"


# ----- escalate skips what a row already decided --------------------------------

def test_escalate_skips_a_problem_whose_row_is_unsolved() -> None:
    from personal_os_worker.jobs.autofill_session import AutofillSession


    api = Api()
    queue_items: list[lane.Problem] = []
    session = AutofillSession.__new__(AutofillSession)
    session.ctx = cast(Any, type("C", (), {
        "fixes": type("F", (), {"submit": staticmethod(queue_items.append)}),
        "dry_run": False, "api": api, "ats": "generic", "run_id": "r",
        "console_errors": [], "knowledge": type("K", (), {"facts": {}})(),
        "title": "t",
    })())
    session.page = cast(Any, type("P", (), {"url": "https://fixture.example/"})())
    session.state = cast(Any, type("S", (), {
        "fix_keys": set(), "issue_ids": {}, "step": 0, "persisted_gaps": set(),
    })())
    session._problem_issue = lambda _f, _k: "unsolved"  # type: ignore[attr-defined]
    finding = cast(Any, type("F", (), {
        "cause": "control_error", "question": "Widget?", "note": "no",
        "evidence": {}, "fact_key": None, "missing_fact": "", "required": False,
    })())
    session.escalate([finding])
    assert queue_items == []


# ----- os hunt fixes --retry -----------------------------------------------------

def test_retry_opens_an_unsolved_problem_and_resets_its_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any,
) -> None:
    import personal_os_worker.jobs.commands as commands

    issue = {"id": "abcdef-1234", "status": "unsolved", "ats": "generic",
             "category": "wall", "signature": "generic|wall|problem|x",
             "field_label": "Apply", "occurrence_count": 1,
             "verification_notes": "attempts 3 of 3; dead end"}
    api = Api({"abcdef-1234": issue})
    api.autofill_failures = lambda status="": list(api.issues.values())  # type: ignore[attr-defined]
    monkeypatch.setattr(commands, "connected", lambda: (None, None))
    monkeypatch.setattr(commands, "WorkerApi", lambda *_a: api)
    assert commands.run_autofill_failure_command("retry", "abcdef") == 0
    assert api.issues["abcdef-1234"]["status"] == "open"
    assert "attempts 0" in api.issues["abcdef-1234"]["verification_notes"]
    # an open row is not retried
    issue2 = dict(issue) | {"id": "fedcba-9999", "status": "open"}
    api.issues["fedcba-9999"] = issue2
    with pytest.raises(RuntimeError, match="open"):
        commands.run_autofill_failure_command("retry", "fedcba")


# ----- one file per type, shared across sites, on trial -----------------------

def _learned_package(tmp_path: Path, category: str = "kinds") -> Path:
    """A temporary learned package wired into the registry."""
    from personal_os_worker.jobs import registry

    folder = tmp_path / category / "learned"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text("")
    package = __import__(
        f"personal_os_worker.jobs.registry.{category}.learned", fromlist=["x"]
    )
    package.__path__ = [str(folder)]  # type: ignore[assignment]
    registry.reload_learned()
    return folder


def test_reuse_adds_a_site_with_no_model_call(repo: Path, tmp_path: Path) -> None:
    """A same-structure declaration that reads back just gains the site."""
    folder = _learned_package(tmp_path)
    (folder / "odd_widget.py").write_text(
        KIND_SOURCE.replace('sites=("fixture.example",)', 'sites=("other.example",)')
    )
    api = Api({"i1": {"status": "open"}})
    calls: list[int] = []

    def runner(command: Any, timeout: float = 0) -> str:
        calls.append(1)
        return "{}"

    probed: list[Path] = []
    queue = FixQueue(
        repo=tmp_path, workdir=tmp_path / "fix", api=api, run_id="run-1",
        report=lambda _l: None, runner=runner,
        prober=lambda r, _a: probed.append(r.learned_file) or {"ok": True, "note": "read back"},
        checker=lambda _d, _f, _p: None,
        keeper=lambda *a, **k: Kept("abc1234", "p", True),
    )
    try:
        problem = dataclasses.replace(
            _problem(structure="odd_widget"), cause="unknown_control"
        )
        assert problem.category == "kinds"
        queue.statuses[problem.key] = __import__(
            "personal_os_worker.jobs.fix.queue", fromlist=["Status"]
        ).Status(problem.key, problem.question, problem.cause)
        assert queue._reuse(problem, _same_structure(problem)) is True
        assert calls == []  # the model was never asked
        assert '"fixture.example"' in (folder / "odd_widget.py").read_text()
        assert api.issues["i1"]["status"] == "fixed"
    finally:
        queue.close()


def test_a_revision_keeps_earlier_examples_and_adds_the_site() -> None:
    """The policy refuses a revised file that drops a site or an example."""
    old = KIND_SOURCE.replace('sites=("fixture.example",)', 'sites=("a.com", "b.com")')
    violations = policy.check_source(old, category="kinds", site="b.com")
    assert violations == []
    dropped = old.replace(', "b.com"', "")
    assert policy.check_source(dropped, category="kinds", site="b.com")


def test_readable_names_and_suffixing(tmp_path: Path) -> None:
    api = Api({})
    queue = _queue(api, tmp_path)
    try:
        problem = _problem()
        answer = lane.Answer("file", "x", "s", "", "odd_widget")
        assert queue._file_name(problem, answer) == "odd_widget"
        # a file by that name for a different structure gets a suffix
        learned = tmp_path / "learned"
        learned.mkdir()
        (learned / "odd_widget.py").write_text(
            'K = Kind(structure="other", name="x", selector="s", reads="v",'
            ' strategies=(), sites=("a.com",))'
        )
        queue._learned_dir = lambda _c: learned  # type: ignore[method-assign]
        assert queue._file_name(problem, answer) == "odd_widget_2"
    finally:
        queue.close()


def test_routes_are_named_by_site() -> None:
    problem = _problem()
    problem = dataclasses.replace(problem, cause="wall")
    assert problem.category == "routes"
    assert lane.name_of(problem) == "fixture_example"


def test_mark_proven_and_retire(repo: Path, tmp_path: Path) -> None:
    from personal_os_worker.jobs.fix.keep import mark_proven, retire

    learned_rel = f"{JOBS}/registry/kinds/learned/odd_widget.py"
    fixture_rel = "workers/tests/fixtures/learned/kinds/odd_widget"
    attempt = _attempt_dir(tmp_path)
    keep(repo, "a fix", attempt, learned_rel, fixture_rel)
    # a later run proves it
    proven = mark_proven(repo, learned_rel)
    assert "proven=True" in (repo / learned_rel).read_text()
    assert proven.pushed
    # a trial failure removes a new file and its fixtures
    old = (repo / learned_rel).read_text()
    retire(repo, learned_rel, fixture_rel, was_new=True)
    assert not (repo / learned_rel).exists()
    assert not (repo / fixture_rel).exists()
    # and a revised file is restored instead
    attempt2 = _attempt_dir(tmp_path / "second", old.replace("proven=True", "proven=False"))
    keep(repo, "a fix", attempt2, learned_rel, fixture_rel)
    (repo / learned_rel).write_text(old + "\n# changed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "revise")
    retire(repo, learned_rel, fixture_rel, was_new=False)
    assert (repo / learned_rel).exists()


def _problem(number: int = 1, *, issue_id: str | None = "i1",
             structure: str = "") -> lane.Problem:
    return lane.Problem(
        key=f"control_error|fixture.example|w{number}",
        cause="control_error",
        question=f"Widget {number}?",
        note="no strategy held it",
        target={"kind": "field", "question": f"Widget {number}?"},
        site="fixture.example",
        issue_id=issue_id,
        run_id="run-1",
        structure=structure,
    )


# ----- trial: proven or retired on a later run --------------------------------

STRATEGY_SOURCE = """from personal_os_worker.jobs.registry.api import Strategy

def set_odd(handle, value):
    handle.fill(value)
    return handle.read()

STRATEGY = Strategy(
    name="odd_set",
    kind="odd_widget",
    operate=set_odd,
    structure="odd_widget",
    sites=("other.example",),
    proven=False,
    examples=(),
)
"""


def _trial(repo: Path, api: Api) -> Trial:
    return Trial(repo=repo, api=api, run_id="run-1")


def test_a_trial_strategy_that_works_becomes_proven(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declaration loaded unproven that fills on a later run flips proven."""
    folder = _learned_package(tmp_path, "strategies")
    source = folder / "odd_set.py"
    source.write_text(STRATEGY_SOURCE)
    _keep_file(repo, tmp_path, source, "strategies")
    tracker = _trial(repo, Api({}))
    module = registry._import_file(source, "strategies")
    strategy = next(v for v in vars(module).values() if isinstance(v, api_Strategy))
    registry._remember(strategy, source)
    assert strategy.proven is False
    assert tracker.report(strategy, True) is True
    learned_rel = "workers/src/personal_os_worker/jobs/registry/strategies/learned/odd_set.py"
    assert "proven=True" in (repo / learned_rel).read_text()


def test_a_trial_strategy_that_fails_is_retired(
    repo: Path, tmp_path: Path
) -> None:
    """A new trial file that fails is removed, fixtures too; a revised file is
    restored to its previous version."""
    folder = _learned_package(tmp_path, "strategies")
    source = folder / "odd_set.py"
    source.write_text(STRATEGY_SOURCE)
    _keep_file(repo, tmp_path, source, "strategies")
    tracker = _trial(repo, Api({}))
    module = registry._import_file(source, "strategies")
    strategy = next(v for v in vars(module).values() if isinstance(v, api_Strategy))
    registry._remember(strategy, source)
    # new file: retired by deletion
    assert tracker.report(strategy, False) is True
    assert not (repo / f"{JOBS}/registry/strategies/learned/odd_set.py").exists()
    assert not (repo / "workers/tests/fixtures/learned/strategies/odd_set").exists()


def test_a_file_kept_this_run_is_neither_proven_nor_retired(tmp_path: Path) -> None:
    tracker = _trial(tmp_path, Api({}))
    path = tmp_path / "odd_set.py"
    path.write_text(STRATEGY_SOURCE)
    module = registry._import_file(path, "strategies")
    strategy = next(v for v in vars(module).values() if isinstance(v, api_Strategy))
    registry._remember(strategy, path)
    tracker.note_kept(path)
    assert tracker.report(strategy, True) is False
    assert tracker.report(strategy, False) is False


def test_a_proven_file_that_fails_is_left_alone(repo: Path, tmp_path: Path) -> None:
    folder = _learned_package(tmp_path, "strategies")
    source = folder / "odd_set.py"
    source.write_text(STRATEGY_SOURCE.replace("proven=False", "proven=True"))
    _keep_file(repo, tmp_path, source, "strategies")
    tracker = _trial(repo, Api({}))
    module = registry._import_file(source, "strategies")
    strategy = next(v for v in vars(module).values() if isinstance(v, api_Strategy))
    assert tracker.report(strategy, False) is False
    assert (repo / f"{JOBS}/registry/strategies/learned/odd_set.py").exists()


def _keep_file(repo: Path, tmp_path: Path, source: Path, category: str) -> None:
    """Commit a learned file + fixtures through keep()."""
    from personal_os_worker.jobs.fix.keep import keep

    attempt = tmp_path / "attempts" / "attempt-1"
    learned = attempt / category / "learned"
    learned.mkdir(parents=True, exist_ok=True)
    (learned / source.name).write_text(source.read_text())
    fixtures = attempt / "fixtures" / source.stem
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / "page.html").write_text("<html/>")
    keep(repo, f"add {source.stem}", attempt,
         f"{JOBS}/registry/{category}/learned/{source.name}",
         f"workers/tests/fixtures/learned/{category}/{source.stem}")



