"""The fixer: a fix is written in a scratch clone, checked, tried live, kept, recorded."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.device.runs import RunManager, RunState
from personal_os_worker.jobs.autofill_session import SessionState
from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.keep import KeepError, Kept, keep
from personal_os_worker.jobs.fix.probe import probe
from personal_os_worker.jobs.fix.queue import FixQueue
from personal_os_worker.jobs.fix.scratch import Change, Scratch, refusal
from personal_os_worker.jobs.resolve import Knowledge

JOBS = "workers/src/personal_os_worker/jobs"
STRATEGIES = (
    '"""Strategies."""\n\nCANDIDATES = {\n    "text": ("set_value",),\n}\n'
    + "".join(f"\n\ndef helper_{index}() -> int:\n    return {index}\n" for index in range(4))
)
SLIDER = STRATEGIES.replace('    "text": ("set_value",),\n', '    "text": ("set_value",),\n'
                            '    "slider": ("drag",),\n')


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
    (work / JOBS).mkdir(parents=True)
    (work / JOBS / "strategies.py").write_text(STRATEGIES)
    (work / JOBS / "other.py").write_text("VALUE = 1\n")
    (work / "workers/tests").mkdir(parents=True)
    (work / "workers/tests/test_existing.py").write_text("def test_x() -> None:\n    pass\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "start")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-q", "origin", "main")
    return work


# ----- what a fix may touch ----------------------------------------------------


def test_a_fix_changes_worker_code_and_adds_tests_only() -> None:
    assert refusal([Change("M", f"{JOBS}/browser.py"),
                    Change("A", "workers/tests/test_fix_abc.py")]) is None
    assert "never changed" in str(refusal([Change("M", f"{JOBS}/safety.py")]))
    assert "never changed" in str(refusal([Change("A", f"{JOBS}/fix/new.py")]))
    assert "never changed" in str(refusal([Change("M", f"{JOBS}/learn.py")]))
    assert "existing test" in str(refusal([Change("M", "workers/tests/test_repair.py")]))
    assert "outside" in str(refusal([Change("M", "backend/src/x.py")]))
    assert "deleted" in str(refusal([Change("D", f"{JOBS}/browser.py")]))
    assert refusal([]) == "the fix changed nothing"


def test_a_scratch_clone_reports_new_and_edited_files_as_one_diff(
    repo: Path, tmp_path: Path
) -> None:
    scratch = Scratch.create(repo, tmp_path / "scratch")
    (scratch.root / JOBS / "strategies.py").write_text(SLIDER)
    scratch.write("workers/tests/test_fix_x.py", b"def test_slider() -> None:\n    pass\n")
    assert sorted(scratch.changes(), key=lambda change: change.path) == [
        Change("M", f"{JOBS}/strategies.py"), Change("A", "workers/tests/test_fix_x.py"),
    ]
    assert '"slider": ("drag",)' in scratch.diff()
    # Nothing reached the live checkout.
    assert "slider" not in (repo / JOBS / "strategies.py").read_text()
    scratch.remove()
    assert not scratch.root.exists()


# ----- keeping a fix -----------------------------------------------------------


def a_fix(repo: Path, tmp_path: Path) -> str:
    scratch = Scratch.create(repo, tmp_path / "scratch")
    (scratch.root / JOBS / "strategies.py").write_text(SLIDER)
    scratch.write("workers/tests/test_fix_x.py", b"def test_slider() -> None:\n    pass\n")
    return scratch.diff()


def test_keeping_a_fix_commits_only_its_hunks_and_leaves_other_work_alone(
    repo: Path, tmp_path: Path
) -> None:
    diff = a_fix(repo, tmp_path)
    # Another session's work: one file staged, another edited, and an edit to
    # the very file the fix changes, on a line the fix does not touch.
    (repo / JOBS / "other.py").write_text("VALUE = 2\n")
    git(repo, "add", f"{JOBS}/other.py")
    (repo / "notes.txt").write_text("someone's notes\n")
    (repo / JOBS / "strategies.py").write_text(
        STRATEGIES.replace("return 3", "return 3  # kept by someone else"))

    kept = keep(repo, diff, "Fix a slider\n\nIt drags.")

    assert kept.pushed and kept.parent != kept.commit
    shown = git(repo, "show", "--name-only", "--format=%an <%ae>%n%s", kept.commit)
    assert shown.splitlines()[:2] == ["ehulewicz <eahtree@gmail.com>", "Fix a slider"]
    assert sorted(shown.split()[-2:]) == sorted([f"{JOBS}/strategies.py",
                                                 "workers/tests/test_fix_x.py"])
    committed = git(repo, "show", f"{kept.commit}:{JOBS}/strategies.py")
    assert "slider" in committed and "someone else" not in committed
    # Their edits are still theirs, uncommitted; the fix is in the files too.
    assert (repo / JOBS / "strategies.py").read_text().count("someone else") == 1
    assert "slider" in (repo / JOBS / "strategies.py").read_text()
    status = git(repo, "status", "--porcelain")
    assert f"M  {JOBS}/other.py" in status and "?? notes.txt" in status
    assert f" M {JOBS}/strategies.py" in status
    assert "test_fix_x.py" not in status
    assert git(repo, "rev-parse", "origin/main").strip() == kept.commit


def test_a_fix_that_conflicts_with_the_checkout_is_not_kept(
    repo: Path, tmp_path: Path
) -> None:
    diff = a_fix(repo, tmp_path)
    (repo / JOBS / "strategies.py").write_text(
        STRATEGIES.replace('"text": ("set_value",)', '"text": ("type",)'))
    before = git(repo, "rev-parse", "HEAD")
    with pytest.raises(KeepError, match="does not apply"):
        keep(repo, diff, "Fix")
    assert git(repo, "rev-parse", "HEAD") == before


def test_a_refused_push_keeps_the_commit_and_says_so(repo: Path, tmp_path: Path) -> None:
    diff = a_fix(repo, tmp_path)
    git(repo, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    kept = keep(repo, diff, "Fix")
    assert not kept.pushed and "not pushed" in kept.note
    assert git(repo, "rev-parse", "HEAD").strip() == kept.commit


# ----- the queue -----------------------------------------------------------------


class Api:
    def __init__(self) -> None:
        self.issues: list[tuple[str, dict[str, Any]]] = []
        self.changes: list[dict[str, Any]] = []

    def update_autofill_failure(self, issue_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self.issues.append((issue_id, changes))
        return {}

    def record_code_change(self, commit: str, **fields: Any) -> dict[str, Any]:
        self.changes.append({"commit": commit} | fields)
        return {}


def slider_problem() -> lane.Problem:
    return lane.Problem(
        key="unknown_control|years", cause="unknown_control", question="Years",
        note="a slider that no control kind operates yet",
        target={"kind": "field", "question": "Years"}, issue_id="issue-1", run_id="run-1",
        page_files={"frame-0.html": b"<div role='slider'></div>", "screen.png": b"png"},
    )


def writes_the_strategy(command: list[str], *, cwd: Path, timeout: float) -> str:
    (cwd / JOBS / "strategies.py").write_text(SLIDER)
    (cwd / "workers/tests/test_fix_slider.py").write_text("def test_it() -> None:\n    pass\n")
    return '{"structured_output": {"kind": "code", "summary": "Drag a slider", ' \
           '"missing_fact": "", "test": "workers/tests/test_fix_slider.py"}}'


def passing(scratch: Scratch, problem: lane.Problem) -> lane.Check:
    return lane.Check(True, "", [lane.Gate("pytest", True, ""),
                                 lane.Gate("replay", True, '{"sampled": 12, "failed": 0}')])


def until(condition: Any, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.02)


def test_a_fix_that_reads_back_live_is_kept_recorded_and_reloads_the_run(
    repo: Path, tmp_path: Path
) -> None:
    api, lines, published = Api(), [], []
    kept: list[str] = []

    def keeper(repo_path: Path, diff: str, message: str) -> Kept:
        kept.append(diff)
        return Kept("abc1234", "def5678", True)

    queue = FixQueue(
        repo=repo, workdir=tmp_path / "fixes", api=api, run_id="run-1", report=lines.append,
        publish=published.append, runner=writes_the_strategy, checker=passing,
        prober=lambda ready, args: {"ok": True, "note": "drag read back 3"}, keeper=keeper,
    )
    assert queue.submit(slider_problem())
    assert not queue.submit(slider_problem())  # one fix per problem a run
    until(lambda: queue.statuses["unknown_control|years"].state == "waiting")
    assert queue.at_safe_point(lambda: {"cdp_url": "x"})
    queue.close()

    assert '"slider": ("drag",)' in kept[0] and "test_fix_slider.py" in kept[0]
    assert "frame-0.html" in kept[0]  # the live page, kept as the test's fixture
    assert [changes["status"] for _, changes in api.issues] == ["fixing", "fixed"]
    assert api.issues[-1][1]["fixed_commit"] == "abc1234"
    [change] = api.changes
    assert change["origin"] == "worker" and change["parent"] == "def5678"
    assert change["failure"] == {"run_id": "run-1", "question_key": "years",
                                 "issue_id": "issue-1"}
    assert change["verification"] == {"sampled": 12, "failed": 0}
    assert change["detail"]["probe"]["note"] == "drag read back 3"
    assert published[-1][0]["state"] == "kept"


def test_a_fix_that_fails_live_goes_back_with_why_then_is_unsolved(
    repo: Path, tmp_path: Path
) -> None:
    api, prompts = Api(), []

    def runner(command: list[str], *, cwd: Path, timeout: float) -> str:
        prompts.append(command[-1])
        return writes_the_strategy(command, cwd=cwd, timeout=timeout)

    queue = FixQueue(
        repo=repo, workdir=tmp_path / "fixes", api=api, run_id="run-1", report=lambda _: None,
        runner=runner, checker=passing,
        prober=lambda ready, args: {"ok": False, "note": "the slider stayed at 0"},
        keeper=lambda *_: pytest.fail("a failed probe must never be kept"),
    )
    queue.submit(slider_problem())
    for attempt in range(1, 4):
        until(lambda attempt=attempt: queue.statuses["unknown_control|years"].state == "waiting"
              and queue.statuses["unknown_control|years"].attempt == attempt)
        assert not queue.at_safe_point(lambda: {})
    until(lambda: queue.statuses["unknown_control|years"].state == "unsolved")
    queue.close()
    assert "the slider stayed at 0" in prompts[1] and "YOUR LAST ATTEMPT FAILED" in prompts[1]
    assert api.issues[-1][1]["status"] == "unsolved"
    assert "3 attempts failed" in api.issues[-1][1]["verification_notes"]


def test_a_missing_fact_is_yours_and_a_model_outage_stops_the_fixer(
    repo: Path, tmp_path: Path
) -> None:
    from personal_os_worker.jobs.model_errors import ModelError

    def missing(command: list[str], *, cwd: Path, timeout: float) -> str:
        return '{"structured_output": {"kind": "missing_fact", "summary": "", ' \
               '"missing_fact": "years of Rust", "test": ""}}'

    queue = FixQueue(repo=repo, workdir=tmp_path / "a", api=Api(), run_id="r",
                     report=lambda _: None, runner=missing, checker=passing)
    queue.submit(slider_problem())
    until(lambda: queue.statuses["unknown_control|years"].state == "missing_fact")
    assert "years of Rust" in queue.statuses["unknown_control|years"].note
    queue.close()

    def outage(command: list[str], *, cwd: Path, timeout: float) -> str:
        raise ModelError("Claude usage limit reached")

    queue = FixQueue(repo=repo, workdir=tmp_path / "b", api=Api(), run_id="r",
                     report=lambda _: None, runner=outage, checker=passing)
    queue.submit(slider_problem())
    until(lambda: queue.model_error is not None)
    other = lane.Problem(key="wall|x", cause="wall", question="Reach the application form",
                         note="", target={"kind": "route"})
    assert not queue.submit(other)
    queue.close()


# ----- the probe -------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser():  # type: ignore[no-untyped-def]
    with sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(headless=True)
        except Error as error:
            pytest.skip(f"browser launch is restricted: {error}")
        yield launched
        launched.close()


@pytest.fixture
def page(browser):  # type: ignore[no-untyped-def]
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()


def test_the_probe_fills_the_target_from_its_fact_and_reads_it_back(page: Any) -> None:
    page.set_content('<label for="y">Years of experience</label><input id="y">')
    knowledge = Knowledge(
        facts={"experience.years": 3}, types={"experience.years": "number"},
        patterns=({"id": "p", "kind": "binding", "status": "active",
                   "question_key": "years of experience", "fact_key": "experience.years",
                   "resolver_id": "binding:p"},),
    )
    result = probe(page, {"kind": "field", "question": "Years of experience"}, knowledge)
    assert result["ok"] and result["filled"] and result["value"] == "3"
    assert page.locator("#y").input_value() == "3"


def test_the_probe_says_when_a_control_is_captured_but_no_fact_answers_it(page: Any) -> None:
    page.set_content('<label for="y">Favourite colour</label><input id="y">')
    result = probe(page, {"kind": "field", "question": "Favourite colour"}, Knowledge())
    assert result["ok"] and not result["filled"] and result["kind"] == "text_entry"
    missing = probe(page, {"kind": "field", "question": "Shoe size"}, Knowledge())
    assert not missing["ok"] and "does not capture" in missing["note"]


# ----- reloading ---------------------------------------------------------------------


def test_a_reload_carries_the_run_state_over() -> None:
    state = SessionState()
    state.step = 2
    state.asked_claude = {"Years": 1}
    state.repair_attempted = {"years"}
    state.seeds = {"Why us?": ("Because", "model")}
    state.held = {("Years", "experience.years"): "waits"}
    state.origins = {"Why us?": ("draft", ("earlier",))}
    state.persisted_gaps = {("fact_gap", "years")}
    state.budget.charge("Years", 3)
    state.fix_keys = {"unknown_control|years"}
    state.issue_ids = {"unknown_control|years": "issue-1"}

    import json

    restored = SessionState.restore(json.loads(json.dumps(state.export())))
    assert restored.step == 2 and restored.asked_claude == {"Years": 1}
    assert restored.repair_attempted == {"years"}
    assert restored.seeds == {"Why us?": ("Because", "model")}
    assert restored.held == state.held and restored.origins == state.origins
    assert restored.persisted_gaps == state.persisted_gaps
    assert restored.budget.spent == {"Years": 3} and restored.budget.total == 3
    assert restored.fix_keys == state.fix_keys and restored.issue_ids == state.issue_ids


def test_the_agent_keeps_a_reloading_runs_place_for_its_next_process() -> None:
    async def publish(_message: dict[str, Any]) -> None:
        return None

    manager = RunManager(publish=publish, update=lambda: "")
    manager.current = RunState(key="k", origin="terminal", phase="running", run_id="run-1",
                               linked=False, reloading=True)
    assert manager._claim({"run_id": "run-2"}) is None
    claimed = manager._claim({"run_id": "run-1"})
    assert claimed is manager.current and not claimed.reloading


# ----- the session hands problems over and reloads -----------------------------------


class Fixes:
    def __init__(self, reload: bool = False) -> None:
        self.problems: list[lane.Problem] = []
        self.reload = reload

    def submit(self, problem: lane.Problem) -> bool:
        self.problems.append(problem)
        return True

    def at_safe_point(self, args: Any) -> bool:
        self.args = args()
        return self.reload


def test_only_what_code_can_settle_goes_to_the_fixer_once(page: Any) -> None:
    from test_autofill_session import audit_session

    from personal_os_worker.jobs.browser import inspect_fields

    page.set_content(
        '<label for="m">Middle name</label><input id="m">'
        '<div><p>Years of programming *</p><div role="slider" aria-valuemin="0" '
        'aria-valuenow="0" aria-required="true" style="width:90px;height:8px"></div></div>'
    )
    session = audit_session(page, facts={})
    session.ctx.use_claude = False
    fixes = Fixes()
    session.ctx.fixes = fixes  # type: ignore[assignment]
    session.persist_step = lambda *_: []  # type: ignore[method-assign]
    session.upload_evidence = lambda _evidence: {}  # type: ignore[method-assign]
    for _ in range(2):
        session.audit_and_repair(inspect_fields(page), {"failures": []})
    [problem] = fixes.problems
    assert (problem.cause, problem.question) == ("unknown_control", "Years of programming")
    assert problem.target == {"kind": "field", "question": "Years of programming"}
    assert any(name.endswith(".html") for name in problem.page_files)
    assert set(fixes.args) == {"cdp_url", "target_id", "page_url", "knowledge"}


def test_a_kept_fix_reloads_the_run_at_the_next_pause(page: Any) -> None:
    from test_autofill_session import audit_session

    from personal_os_worker.jobs.autofill_session import ReloadForFix

    page.set_content('<label for="m">Middle name</label><input id="m">')
    session = audit_session(page, facts={})
    session.ctx.fixes = Fixes(reload=True)  # type: ignore[assignment]
    saved: list[bool] = []
    session.save_now = lambda: saved.append(True) or 0  # type: ignore[method-assign]
    with pytest.raises(ReloadForFix):
        session.sleep()
    assert saved == [True]
