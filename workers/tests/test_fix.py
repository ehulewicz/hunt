"""The fixer: a file is drafted, checked, tried live, kept, and loaded."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.keep import Kept, keep
from personal_os_worker.jobs.fix.queue import MAX_ATTEMPTS, FixQueue
from personal_os_worker.jobs.registry import policy

JOBS = "workers/src/personal_os_worker/jobs"

KIND_SOURCE = '''from personal_os_worker.jobs.registry.api import Example, Kind

KIND = Kind(
    name="odd_widget",
    selector="[data-widget=odd]",
    reads="value",
    strategies=(),
    site="fixture.example",
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


def _problem(number: int = 1, *, issue_id: str | None = "i1") -> lane.Problem:
    return lane.Problem(
        key=f"control_error|fixture.example|w{number}",
        cause="control_error",
        question=f"Widget {number}?",
        note="no strategy held it",
        target={"kind": "field", "question": f"Widget {number}?"},
        site="fixture.example",
        issue_id=issue_id,
        run_id="run-1",
    )


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
