"""The fixer: a fix is written in a scratch clone, checked, tried live, kept, recorded."""

from __future__ import annotations

import importlib
import subprocess
import threading
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


@pytest.mark.parametrize("path", [
    f"{JOBS}/../model.py", f"{JOBS}/fix/../browser.py", f"{JOBS}//safety.py",
    f"{JOBS}/safety/__init__.py", f"{JOBS}/model.pyc", f"{JOBS}/fix.py",
    "workers/tests/conftest.py", "workers/tests/nested/conftest.py",
    "workers/tests/__init__.py", "workers/tests/sitecustomize.py",
])
def test_refusal_rejects_path_and_import_bypasses(path: str) -> None:
    assert refusal([Change("A", path)]) is not None


def test_scratch_rejects_symlinks_and_mode_changes(repo: Path, tmp_path: Path) -> None:
    scratch = Scratch.create(repo, tmp_path / "scratch")
    (scratch.root / JOBS / "other.py").chmod(0o755)
    assert refusal(scratch.changes()) is not None
    (scratch.root / JOBS / "other.py").chmod(0o644)
    (scratch.root / JOBS / "new.py").symlink_to(repo / JOBS / "other.py")
    assert refusal(scratch.changes()) is not None


@pytest.mark.parametrize("relative", ["../outside.txt", "/tmp/fixer-escape.txt", JOBS + "/link/x"])
def test_scratch_write_cannot_escape_clone(repo: Path, tmp_path: Path, relative: str) -> None:
    scratch = Scratch.create(repo, tmp_path / "scratch")
    (scratch.root / JOBS / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        scratch.write(relative, b"escaped")
    assert not (tmp_path / "x").exists()


def test_scratch_base_cannot_be_moved_to_hide_changes(repo: Path, tmp_path: Path) -> None:
    scratch = Scratch.create(repo, tmp_path / "scratch")
    base = scratch.base
    (scratch.root / JOBS / "other.py").write_text("VALUE = 2\n")
    git(scratch.root, "-c", "user.name=test", "-c", "user.email=test@example.com",
        "commit", "-qam", "hide")
    assert scratch.base == base
    assert Change("M", f"{JOBS}/other.py") in scratch.changes()


@pytest.mark.parametrize("answer", [
    "not json", "[]", '{"kind": "wrong"}',
    '{"kind": "code", "summary": [], "missing_fact": "", "test": "x"}',
    '{"kind": "code"}',
])
def test_invalid_model_answers_are_never_success(answer: str) -> None:
    assert lane.answer_of(answer) is None


def test_code_answer_requires_the_named_new_regression_test(repo: Path, tmp_path: Path) -> None:
    scratch = Scratch.create(repo, tmp_path / "scratch")
    (scratch.root / JOBS / "strategies.py").write_text(SLIDER)
    checked = lane.check(scratch, slider_problem())
    assert not checked.passed and "test" in checked.feedback.lower()


def test_keep_preserves_staged_edits_to_the_same_file(repo: Path, tmp_path: Path) -> None:
    diff = a_fix(repo, tmp_path)
    staged = STRATEGIES.replace("return 3", "return 33")
    (repo / JOBS / "strategies.py").write_text(staged)
    git(repo, "add", f"{JOBS}/strategies.py")
    (repo / JOBS / "strategies.py").write_text(staged.replace("return 2", "return 22"))
    kept = keep(repo, diff, "fix", push=False)
    assert "return 33" not in git(repo, "show", f"{kept.commit}:{JOBS}/strategies.py")
    index = git(repo, "show", f":{JOBS}/strategies.py")
    assert "slider" in index and "return 33" in index and "return 22" not in index
    assert "return 22" in (repo / JOBS / "strategies.py").read_text()


def test_keep_apply_failure_leaves_head_index_and_worktree_unchanged(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    keeping = importlib.import_module("personal_os_worker.jobs.fix.keep")
    diff = a_fix(repo, tmp_path)
    parent = git(repo, "rev-parse", "HEAD")
    before = git(repo, "write-tree")
    original = keeping._git

    def fail_apply(repo: Path, *args: str, **kwargs: Any) -> Any:
        if args[0] == "apply" and len(args) == 2:
            raise KeepError("injected worktree failure")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(keeping, "_git", fail_apply)
    with pytest.raises(KeepError):
        keep(repo, diff, "fix", push=False)
    assert git(repo, "rev-parse", "HEAD") == parent
    assert git(repo, "write-tree") == before
    assert (repo / JOBS / "strategies.py").read_text() == STRATEGIES
    assert not (repo / "workers/tests/test_fix_x.py").exists()


def test_probe_route_is_guarded_even_when_route_clicks_submit(
    page: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_os_worker.jobs import routes

    page.set_content('<button onclick="window.submitted=true">Submit application</button>')

    def route(page: Any, target: Any) -> None:
        page.locator("button").click()

    monkeypatch.setattr(routes, "route_for", lambda _: route)
    result = probe(page, {"kind": "route", "goal": "form", "title": "Engineer",
                          "company": "Example"}, Knowledge())
    assert not result["ok"]
    assert page.evaluate("window.submitted === true") is False


def test_probe_crash_returns_failure(page: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    probing = importlib.import_module("personal_os_worker.jobs.fix.probe")

    def crash(*args: Any) -> Any:
        raise ValueError("broken capture")

    monkeypatch.setattr(probing, "probe_field", crash)
    assert probe(page, {"kind": "field", "question": "x"}, Knowledge())["ok"] is False


def test_close_during_model_call_finishes_without_waiting_for_safe_point(
    repo: Path, tmp_path: Path,
) -> None:
    entered, release = threading.Event(), threading.Event()
    api = Api()

    def blocked(*args: Any, **kwargs: Any) -> str:
        entered.set()
        assert release.wait(5)
        return writes_the_strategy(*args, **kwargs)

    queue = FixQueue(repo=repo, workdir=tmp_path / "fixes", api=api, run_id="r",
                     report=lambda _: None, runner=blocked, checker=passing)
    queue.submit(slider_problem())
    assert entered.wait(5)
    queue.close()
    release.set()
    queue._thread.join(5)
    assert not queue._thread.is_alive()
    assert api.issues[-1][1]["status"] == "unsolved"
    assert not queue.submit(lane.Problem("other", "wall", "q", "", {"kind": "route"}))


def test_probe_exception_releases_waiting_attempt(repo: Path, tmp_path: Path) -> None:
    def crash(*args: Any) -> Any:
        raise ValueError("probe crashed")

    queue = FixQueue(repo=repo, workdir=tmp_path / "fixes", api=Api(), run_id="r",
                     report=lambda _: None, runner=writes_the_strategy, checker=passing,
                     prober=crash)
    queue.submit(slider_problem())
    until(lambda: queue.statuses[slider_problem().key].state == "waiting")
    try:
        assert queue.at_safe_point(lambda: {}) is False
    finally:
        queue.close()
        queue._thread.join(5)
    assert not queue._thread.is_alive()


def test_model_file_tools_are_confined_to_scratch(repo: Path, tmp_path: Path) -> None:
    scratch = Scratch.create(repo, tmp_path / "scratch")

    def runner(command: list[str], *, cwd: Path, timeout: float) -> str:
        assert cwd == scratch.root
        assert "--restricted" in command and "--strict-mcp-config" in command
        assert command[command.index("--tools") + 1] == "Read,Edit,Write,Glob,Grep"
        return '{"kind":"impossible","summary":"no route","missing_fact":"","test":""}'

    assert lane.write(scratch, slider_problem(), [], None, runner=runner) is not None


def test_guard_survives_route_navigation_and_restores_manual_submit(
    page: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_os_worker.jobs import routes

    def route(page: Any, target: Any) -> None:
        page.goto("data:text/html,<button onclick='window.submitted=true'>Submit</button>")
        page.locator("button").click()

    monkeypatch.setattr(routes, "route_for", lambda _: route)
    probe(page, {"kind": "route", "goal": "form", "title": "Engineer",
                 "company": "Example"}, Knowledge())
    assert page.evaluate("window.submitted === true") is False
    page.locator("button").click()
    assert page.evaluate("window.submitted === true") is True


def test_crashed_probe_process_cannot_report_success(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_os_worker.jobs.fix.queue import Ready, run_probe

    scratch = Scratch.create(repo, tmp_path / "scratch")
    ready = Ready(slider_problem(), scratch, lane.Answer("code", "x"), lane.Check(True, ""), 1)
    def crashed(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["cwd"] == scratch.root / "workers"
        assert kwargs["env"]["PYTHONPATH"] == str(scratch.root / "workers/src")
        return subprocess.CompletedProcess(args[0], 1, '{"ok":true}\n', "crashed after printing")

    monkeypatch.setattr(subprocess, "run", crashed)
    assert run_probe(ready, {})["ok"] is False


def test_keep_handles_binary_new_files_and_quoted_paths(repo: Path, tmp_path: Path) -> None:
    scratch = Scratch.create(repo, tmp_path / "scratch")
    path = "workers/tests/fixtures/a space é.png"
    content = bytes(range(256))
    scratch.write(path, content)
    kept = keep(repo, scratch.diff(), "fixture", push=False)
    assert (repo / path).read_bytes() == content
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "diff", kept.parent, kept.commit, "--name-only").strip()


def test_keep_does_not_take_an_index_lock_held_by_another_session(
    repo: Path, tmp_path: Path,
) -> None:
    diff = a_fix(repo, tmp_path)
    lock = repo / ".git/index.lock"
    lock.write_text("another session")
    parent = git(repo, "rev-parse", "HEAD")
    with pytest.raises(KeepError):
        keep(repo, diff, "fix", push=False)
    assert lock.read_text() == "another session"
    assert git(repo, "rev-parse", "HEAD") == parent


def test_keep_retries_a_moved_head_without_sweeping_in_other_work(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    keeping = importlib.import_module("personal_os_worker.jobs.fix.keep")
    transaction = keeping._ref_transaction
    moved: list[str] = []

    @contextmanager
    def move_once(repo: Path, commit: str, parent: str) -> Any:
        if not moved:
            tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
            other = subprocess.run(["git", "commit-tree", tree, "-p", parent], cwd=repo,
                                   input="other session\n", capture_output=True, text=True,
                                   check=True).stdout.strip()
            git(repo, "update-ref", "refs/heads/main", other, parent)
            moved.append(other)
        with transaction(repo, commit, parent):
            yield

    monkeypatch.setattr(keeping, "_ref_transaction", move_once)
    kept = keep(repo, a_fix(repo, tmp_path), "fix", push=False)
    assert kept.parent == moved[0]
    assert git(repo, "status", "--porcelain") == ""


def test_index_install_failure_rolls_back_worktree(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    keeping = importlib.import_module("personal_os_worker.jobs.fix.keep")
    diff = a_fix(repo, tmp_path)
    parent = git(repo, "rev-parse", "HEAD")

    def fail(*args: Any) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(keeping, "_replace_index", fail)
    with pytest.raises(KeepError):
        keep(repo, diff, "fix", push=False)
    assert git(repo, "rev-parse", "HEAD") == parent
    assert git(repo, "status", "--porcelain") == ""


def test_close_waiting_attempt_removes_scratch_and_unsolves_issue(
    repo: Path, tmp_path: Path,
) -> None:
    api = Api()
    queue = FixQueue(repo=repo, workdir=tmp_path / "fixes", api=api, run_id="r",
                     report=lambda _: None, runner=writes_the_strategy, checker=passing)
    queue.submit(slider_problem())
    until(lambda: queue.statuses[slider_problem().key].state == "waiting")
    queue.close()
    queue._thread.join(5)
    assert not queue._thread.is_alive()
    assert api.issues[-1][1]["status"] == "unsolved"
    assert not (tmp_path / "fixes" / slider_problem().slug).exists()


def test_metadata_and_ui_failures_do_not_repeat_a_kept_fix(repo: Path, tmp_path: Path) -> None:
    class Offline(Api):
        def record_code_change(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("offline")

    def disconnected(*args: Any) -> Any:
        raise OSError("disconnected")

    queue = FixQueue(repo=repo, workdir=tmp_path / "fixes", api=Offline(), run_id="r",
                     report=disconnected, publish=disconnected, runner=writes_the_strategy,
                     checker=passing, prober=lambda *_: {"ok": True},
                     keeper=lambda *_: Kept("abc", "def", True))
    queue.submit(slider_problem())
    until(lambda: queue.statuses[slider_problem().key].state == "waiting")
    assert queue.at_safe_point(lambda: {})
    queue.close()
    queue._thread.join(5)
    assert queue.statuses[slider_problem().key].state == "kept"


def test_gates_cannot_change_the_code_that_will_be_probed(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch = Scratch.create(repo, tmp_path / "scratch")
    (scratch.root / JOBS / "strategies.py").write_text(SLIDER)
    scratch.write(f"workers/tests/test_fix_{slider_problem().slug}.py", b"def test_x(): pass\n")

    def mutating_gates(self: Scratch) -> list[lane.Gate]:
        (self.root / JOBS / "safety.py").write_text("# removed the guards\n")
        return [lane.Gate("pytest", True, "")]

    monkeypatch.setattr(Scratch, "gates", mutating_gates)
    monkeypatch.setattr(Scratch, "replay", lambda *args, **kwargs: lane.Gate("replay", True, ""))
    assert not lane.check(scratch, slider_problem()).passed


def test_ignored_python_can_never_bypass_refusal(repo: Path, tmp_path: Path) -> None:
    scratch = Scratch.create(repo, tmp_path / "scratch")
    (scratch.root / ".git/info/exclude").write_text("*.pyc\n")
    scratch.write(f"{JOBS}/model.pyc", b"untrusted bytecode")
    scratch.write("workers/tests/test_new.py", b"def test_x(): pass\n")
    assert refusal(scratch.changes()) is not None


def test_failed_keep_retries_then_unsolves_without_recording_a_fix(
    repo: Path, tmp_path: Path,
) -> None:
    api = Api()
    calls: list[str] = []

    def refuse(*args: Any) -> Kept:
        calls.append("keep")
        raise KeepError("conflict")

    queue = FixQueue(repo=repo, workdir=tmp_path / "fixes", api=api, run_id="r",
                     report=lambda _: None, runner=writes_the_strategy, checker=passing,
                     prober=lambda *_: {"ok": True}, keeper=refuse)
    queue.submit(slider_problem())
    for attempt in range(1, 4):
        until(lambda attempt=attempt: queue.statuses[slider_problem().key].state == "waiting"
              and queue.statuses[slider_problem().key].attempt == attempt)
        assert not queue.at_safe_point(lambda: {})
    until(lambda: queue.statuses[slider_problem().key].state == "unsolved")
    queue.close()
    queue._thread.join(5)
    assert len(calls) == 3 and not api.changes and not queue.has_kept()
    assert [changes["status"] for _, changes in api.issues] == ["fixing", "unsolved"]


def test_changing_scratch_after_a_probe_never_keeps_untested_code(
    repo: Path, tmp_path: Path,
) -> None:
    def mutate(ready: Any, args: Any) -> dict[str, Any]:
        (ready.scratch.root / JOBS / "other.py").write_text("VALUE = 9000\n")
        return {"ok": True}

    queue = FixQueue(repo=repo, workdir=tmp_path / "fixes", api=Api(), run_id="r",
                     report=lambda _: None, runner=writes_the_strategy, checker=passing,
                     prober=mutate, keeper=lambda *_: pytest.fail("untested patch was kept"))
    queue.submit(slider_problem())
    until(lambda: queue.statuses[slider_problem().key].state == "waiting")
    try:
        assert not queue.at_safe_point(lambda: {})
        assert not queue.has_kept()
    finally:
        queue.close()
        queue._thread.join(5)


def test_close_during_failed_check_does_not_start_another_model_attempt(
    repo: Path, tmp_path: Path,
) -> None:
    entered, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def runner(*args: Any, **kwargs: Any) -> str:
        calls.append("model")
        return writes_the_strategy(*args, **kwargs)

    def blocked_check(*args: Any) -> lane.Check:
        entered.set()
        assert release.wait(5)
        return lane.Check(False, "test failed")

    queue = FixQueue(repo=repo, workdir=tmp_path / "fixes", api=Api(), run_id="r",
                     report=lambda _: None, runner=runner, checker=blocked_check)
    queue.submit(slider_problem())
    assert entered.wait(5)
    queue.close()
    release.set()
    queue._thread.join(5)
    assert not queue._thread.is_alive()
    assert calls == ["model"]
