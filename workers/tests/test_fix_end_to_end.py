"""A fixer candidate, end to end: written or reused, checked, probed, kept.

The real checker (policy, ruff, mypy, the file's own examples) and the real
keep (exactly two paths, one commit on main, pushed) run here; only the model
runner and the live-tab prober are faked. The kinds learned package points at
the temp repo's learned dir, so the queue sees what its commits write.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Api, git

import personal_os_worker.jobs.registry as registry
from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.keep import fixture_rel, learned_rel
from personal_os_worker.jobs.fix.queue import FixQueue, Status

try:
    from playwright.sync_api import Error, sync_playwright
except ImportError:  # pragma: no cover
    Error = Exception

EXAMPLES = Path(__file__).parent / "fixtures" / "learned_examples"
RADIOS = (EXAMPLES / "kinds" / "radios.html").read_bytes()
ARGS = {"cdp_url": "x", "target_id": "t", "page_url": "u", "knowledge": {}}
SITE = "fixture.example"
OTHER = "other.example"


@pytest.fixture(scope="module")
def chromium() -> None:
    """The checker's examples subprocess needs a launchable headless browser.

    Launch and close it at setup: a live sync playwright would block the
    examples runner's own launch inside the test.
    """
    try:
        with sync_playwright() as playwright:
            playwright.chromium.launch(headless=True).close()
    except Error as error:
        pytest.skip(f"browser launch is restricted: {error}")


@pytest.fixture
def learned_kinds(repo: Path) -> Iterator[Path]:
    """The kinds learned package pointed at the temp repo's learned dir."""
    folder = repo / "workers/src/personal_os_worker/jobs/registry/kinds/learned"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text("")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "learned package")
    git(repo, "push", "-q", "origin", "main")
    package = __import__(
        "personal_os_worker.jobs.registry.kinds.learned", fromlist=["x"]
    )
    original = list(package.__path__)
    package.__path__ = [str(folder)]  # type: ignore[assignment]
    registry.reload_learned()
    try:
        yield folder
    finally:
        package.__path__ = original  # type: ignore[assignment]
        registry.reload_learned()


def _problem(structure: str = "odd_widget") -> lane.Problem:
    return lane.Problem(
        key=f"unknown_control|{SITE}|w1",
        cause="unknown_control",
        question="Employed?",
        note="no kind captured the widget",
        target={"kind": "field", "question": "Employed?"},
        site=SITE,
        issue_id="i1",
        run_id="run-1",
        structure=structure,
        page_files={
            "frame-0.html": RADIOS,
            "controls.json": b"[{}]",
            "screen.png": b"\x89PNG\r\n\x1a\n",
        },
    )


def _kind_answer(
    *,
    name: str,
    sites: tuple[str, ...],
    structure: str = "odd_widget",
    proven: bool = False,
    seen: tuple[tuple[str, str], ...],
) -> str:
    """One model answer: a Kind whose examples read #opt-yes's text ('Yes')."""
    sites_src = "".join(f'        "{site}",\n' for site in sites)
    seen_src = "".join(
        f'''        Example(
            fixture="{fixture}",
            selector="#opt-yes",
            value="",
            expected="{expected}",
        ),
'''
        for fixture, expected in seen
    )
    file = f'''from personal_os_worker.jobs.registry.api import Example, Kind

KIND = Kind(
    name="{name}",
    selector='div[role="radio"]',
    reads="text",
    strategies=("click_div",),
    sites=(
{sites_src}    ),
    structure="{structure}",
    proven={proven},
    examples=(
{seen_src}    ),
)
'''
    return json.dumps({
        "kind": "file", "file": file, "name": name, "missing_fact": "",
        "summary": f"a {name} kind for the site's radio group",
    })


def _drive(queue: FixQueue, key: str) -> Status:
    """at_safe_point from this thread until the problem settles."""
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        queue.at_safe_point(lambda: dict(ARGS))
        status = queue.statuses[key]
        if status.state in {"kept", "unsolved", "missing_fact"}:
            return status
        time.sleep(0.02)
    raise AssertionError(f"the fix never settled: {queue.statuses[key].item()}")


def _queue(
    api: Api, repo: Path, tmp_path: Path, *, runner: Any, prober: Any,
    loader: Any = None,
) -> FixQueue:
    return FixQueue(
        repo=repo, workdir=tmp_path / "fix", api=api, run_id="run-1",
        report=lambda _line: None, runner=runner, prober=prober, loader=loader,
    )


def _seed_kind(repo: Path, learned: Path) -> None:
    """A committed kind for other.example, with its own fixture folder."""
    source = json.loads(_kind_answer(
        name="odd_widget", sites=(OTHER,), seen=(("other-page.html", "Yes"),)
    ))["file"]
    (learned / "odd_widget.py").write_text(source)
    fixtures = repo / fixture_rel("kinds", "odd_widget")
    fixtures.mkdir(parents=True, exist_ok=True)
    (fixtures / "other-page.html").write_bytes(RADIOS)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed kind")
    git(repo, "push", "-q", "origin", "main")
    registry.reload_learned()


# ----- a new fix: drafted, checked, probed, kept --------------------------------


def test_a_new_fix_is_checked_probed_and_kept(
    repo: Path, learned_kinds: Path, tmp_path: Path, chromium: None
) -> None:
    api = Api({"i1": {"status": "open"}})
    frame = lane.fixture_name(SITE, "frame-0.html")
    calls: list[list[str]] = []
    loaded: list[tuple[Path, str]] = []

    def runner(command: list[str], timeout: float = 0) -> str:
        calls.append(command)
        return _kind_answer(
            name="div_radio", sites=(SITE,), seen=((frame, "Yes"),)
        )

    queue = _queue(
        api, repo, tmp_path, runner=runner,
        prober=lambda _ready, _args: {"ok": True, "note": "read back"},
        loader=lambda path, category: loaded.append((path, category)),
    )
    try:
        problem = _problem()
        assert queue.submit(problem)
        status = _drive(queue, problem.key)
        assert status.state == "kept", status.note
    finally:
        queue.close()

    learned = learned_rel("kinds", "div_radio")
    fixtures = fixture_rel("kinds", "div_radio")
    show = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert set(show) == {
        learned,
        f"{fixtures}/{SITE.replace('.', '_')}-frame-0.html",
        f"{fixtures}/{SITE.replace('.', '_')}-controls.json",
    }  # two sanitized page files, no screen.png
    head = git(repo, "rev-parse", "HEAD").strip()
    assert git(repo, "ls-remote", "origin", "main").split()[0] == head
    assert git(repo, "show", "-s", "--format=%an", "HEAD").strip() == "ehulewicz"
    assert api.code_changes[0]["detail"]["files"] == [learned]
    assert loaded == [(repo / learned, "kinds")]
    # the checker's CLI once more, against the kept file as committed
    completed = subprocess.run(
        [sys.executable, "-m", "personal_os_worker.jobs.fix.examples",
         str(repo / learned), str(repo / fixtures)],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


# ----- reuse: a same-structure file just gains the site -------------------------


def test_a_same_structure_file_gains_the_site_without_a_model_call(
    repo: Path, learned_kinds: Path, tmp_path: Path, chromium: None
) -> None:
    _seed_kind(repo, learned_kinds)
    api = Api({"i1": {"status": "open"}})
    calls: list[int] = []
    probes: list[tuple[threading.Thread, dict[str, Any]]] = []

    def runner(command: Any, timeout: float = 0) -> str:
        calls.append(1)
        return "{}"

    def prober(_ready: Any, args: dict[str, Any]) -> dict[str, Any]:
        probes.append((threading.current_thread(), args))
        return {"ok": True, "note": "read back"}

    queue = _queue(api, repo, tmp_path, runner=runner, prober=prober)
    try:
        problem = _problem()
        assert queue.submit(problem)
        status = _drive(queue, problem.key)
        assert status.state == "kept", status.note
    finally:
        queue.close()

    assert calls == []  # the model was never asked
    probe_thread, probe_args = probes[0]
    assert probe_thread == threading.current_thread()  # the safe point's thread
    assert probe_args == ARGS
    learned = learned_rel("kinds", "odd_widget")
    content = (repo / learned).read_text()
    assert f'        "{OTHER}",\n        "{SITE}",\n' in content  # one per line
    fixtures = fixture_rel("kinds", "odd_widget")
    show = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert learned in show
    assert f"{fixtures}/{SITE.replace('.', '_')}-frame-0.html" in show
    assert f"{fixtures}/{SITE.replace('.', '_')}-controls.json" in show
    assert git(repo, "status", "--porcelain") == ""
    assert api.code_changes[0]["detail"]["reused"] is True


# ----- a revision keeps its stem, its earlier sites, its earlier examples -------


def test_a_new_kind_when_no_reuse_reads_back(
    repo: Path, learned_kinds: Path, tmp_path: Path, chromium: None
) -> None:
    """Kinds are never revised: reuse fails, one model call, a new file kept."""
    _seed_kind(repo, learned_kinds)
    api = Api({"i1": {"status": "open"}})
    frame = lane.fixture_name(SITE, "frame-0.html")
    answers = iter([
        _kind_answer(name="div_radio", sites=(SITE,), seen=((frame, "Yes"),))
    ])
    prompts: list[str] = []

    def runner(command: list[str], timeout: float = 0) -> str:
        prompts.append(command[-1])
        return next(answers)

    def prober(ready: Any, _args: dict[str, Any]) -> dict[str, Any]:
        return (
            {"ok": False, "note": "no readback"}
            if ready.reuse else {"ok": True, "note": "read back"}
        )

    queue = _queue(api, repo, tmp_path, runner=runner, prober=prober)
    try:
        problem = _problem()
        assert queue.submit(problem)
        status = _drive(queue, problem.key)
        assert status.state == "kept", status.note
    finally:
        queue.close()

    assert len(prompts) == 1
    learned = learned_rel("kinds", "div_radio")
    content = (repo / learned).read_text()
    assert f'"{SITE}"' in content
    subject = git(repo, "log", "-1", "--format=%s").strip()
    assert subject == "Fix unknown control for 'Employed?'"


# ----- units --------------------------------------------------------------------


def test_the_prompt_names_the_structure_and_site_named_fixtures() -> None:
    problem = _problem()
    text = lane.prompt(problem, None)
    assert repr(problem.structure) in text
    assert "fixture_example-frame-0.html" in text
    assert "fixture_example-controls.json" in text
    assert ".png" not in text
    assert problem.slug not in text


def test_a_route_file_is_named_for_its_site_and_goal() -> None:
    wall = dataclasses.replace(_problem(), cause="wall")
    posting = dataclasses.replace(_problem(), cause="posting_not_found")
    assert lane.name_of(wall) != lane.name_of(posting)


# ----- recheck, reuse batches, and open rows ------------------------------------


def test_one_probe_call_tries_every_reuse_candidate(
    repo: Path, learned_kinds: Path, tmp_path: Path
) -> None:
    _seed_kind(repo, learned_kinds)
    (learned_kinds / "second_kind.py").write_text(
        json.loads(_kind_answer(
            name="second_kind", sites=(OTHER,), seen=(("other-page.html", "Yes"),)
        ))["file"].replace('"odd_widget"', '"second_kind"')
    )
    api = Api({"i1": {"status": "open"}})
    probes: list[list[str]] = []
    calls: list[list[str]] = []

    def prober(ready: Any, _args: dict[str, Any]) -> dict[str, Any]:
        probes.append([str(c.learned_file) for c in ready.candidates])
        # the second candidate passes
        return {"ok": True, "note": "read back",
                "file": probes[0][1]}

    queue = _queue(
        api, repo, tmp_path,
        runner=lambda c, timeout=0: calls.append(c) or "",
        prober=prober,
    )
    try:
        problem = _problem()
        assert queue.submit(problem)
        assert _drive(queue, problem.key).state == "kept"
    finally:
        queue.close()
    assert calls == []  # reuse never asks the model
    assert len(probes) == 1
    assert len(probes[0]) == 2
    # the passing candidate gained the site; the first file is untouched
    assert '"fixture.example"' in (repo / learned_rel("kinds", "second_kind")).read_text()
    assert '"fixture.example"' not in (
        repo / learned_rel("kinds", "odd_widget")
    ).read_text()


def test_ten_identical_widgets_cost_one_fix(
    repo: Path, learned_kinds: Path, tmp_path: Path, chromium: None
) -> None:
    api = Api({f"i{i}": {"status": "open"} for i in range(10)})
    calls = iter([
        _kind_answer(
            name="div_radio", sites=(SITE,),
            seen=((lane.fixture_name(SITE, "frame-0.html"), "Yes"),),
        )
    ])
    queue = _queue(
        api, repo, tmp_path,
        runner=lambda _c, timeout=0: next(calls),
        prober=lambda _r, _a: {"ok": True, "note": "read back"},
    )
    try:
        problems = [
            dataclasses.replace(
                _problem(), key=f"unknown_control|{SITE}|w{i}",
                question=f"Q{i}?", issue_id=f"i{i}",
            )
            for i in range(10)
        ]
        for problem in problems:
            assert queue.submit(problem)
        def recheck(_p: Any) -> str:
            return "answered" if queue.has_kept() else "persists"

        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            for each in problems:
                queue.at_safe_point(lambda: dict(ARGS), recheck)
            states = {p.key: queue.statuses[p.key].state for p in problems}
            if all(state == "kept" for state in states.values()):
                break
            time.sleep(0.02)
        assert all(state == "kept" for state in states.values()), states
    finally:
        queue.close()
    fixed = [u for u in api.updates if u.get("status") == "fixed"
             and "already answers" in str(u.get("verification_notes") or "")]
    assert len(fixed) == 9


def test_a_gone_problem_keeps_its_row_open(tmp_path: Path) -> None:
    api = Api({"i1": {"status": "open", "verification_notes": "attempts 1 of 3"}})
    threads: list[threading.Thread] = []
    queue = _queue(
        api, tmp_path, tmp_path,
        runner=lambda _c, timeout=0: pytest.fail("model called"),
        prober=lambda _r, _a: {"ok": True},
    )

    def recheck(_p: Any) -> str:
        threads.append(threading.current_thread())
        return "gone"

    try:
        problem = _problem()
        assert queue.submit(problem)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            queue.at_safe_point(lambda: dict(ARGS), recheck)
            if queue.statuses[problem.key].state == "later":
                break
            time.sleep(0.02)
        assert queue.statuses[problem.key].state == "later"
        assert api.issues["i1"]["status"] == "open"
        assert "attempts 1 of 3" in api.issues["i1"]["verification_notes"]
        assert threads and threads[0] == threading.current_thread()
        assert queue._consecutive_unsolved == 0  # "later" leaves the brake
    finally:
        queue.close()


# ----- the problem lifecycle: only three real attempts make unsolved -------------


def _wait_for(queue: FixQueue, key: str, *states: str, timeout: float = 60) -> Status:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = queue.statuses[key]
        if status.state in states:
            return status
        time.sleep(0.02)
    raise AssertionError(f"{key} never reached {states}: {queue.statuses[key].item()}")


def test_the_brake_leaves_a_queued_problem_open(tmp_path: Path) -> None:
    api = Api({f"i{i}": {"status": "open"} for i in range(4)})
    calls: list[list[str]] = []
    queue = _queue(
        api, tmp_path, tmp_path,
        runner=lambda c, timeout=0: calls.append(c) or _kind_answer(
            name="w", sites=(SITE,), seen=()
        ),
        prober=lambda _r, _a: {"ok": True},
    )
    queue.checker = lambda *_args: "refused every time"
    try:
        problems = [
            dataclasses.replace(_problem(), key=f"unknown_control|{SITE}|w{i}",
                                issue_id=f"i{i}")
            for i in range(4)
        ]
        for problem in problems:
            assert queue.submit(problem)
        for problem in problems[:3]:
            _wait_for(queue, problem.key, "unsolved")
        status = _wait_for(queue, problems[3].key, "later")
        assert status.state == "later"
        assert api.issues["i3"]["status"] == "open"
        # three attempts each for the first three, none for the fourth
        assert len(calls) == 9
    finally:
        queue.close()


def test_a_model_outage_leaves_the_row_open(tmp_path: Path) -> None:
    from personal_os_worker.jobs.model_errors import ModelError

    api = Api({"i1": {"status": "open"}})

    def runner(_c: list[str], timeout: float = 0) -> str:
        raise ModelError("the model is unavailable")

    queue = _queue(api, tmp_path, tmp_path, runner=runner,
                   prober=lambda _r, _a: {"ok": True})
    try:
        problem = _problem()
        assert queue.submit(problem)
        _wait_for(queue, problem.key, "later")
        assert api.issues["i1"]["status"] == "open"
        assert "attempts 1 of 3" in api.issues["i1"]["verification_notes"]
    finally:
        queue.close()


def test_closing_before_the_first_attempt_leaves_the_row_open(tmp_path: Path) -> None:
    api = Api({"i1": {"status": "open"}})
    queue = _queue(
        api, tmp_path, tmp_path,
        runner=lambda _c, timeout=0: pytest.fail("model called"),
        prober=lambda _r, _a: {"ok": True},
    )
    # a recheck-capable caller, so the fixer waits on its recheck request
    queue.at_safe_point(lambda: dict(ARGS), lambda _p: "persists")
    problem = _problem()
    assert queue.submit(problem)
    time.sleep(0.3)
    queue.close()
    assert _wait_for(queue, problem.key, "later").state == "later"
    assert api.issues["i1"]["status"] == "open"


def test_closing_mid_attempt_leaves_the_row_open(tmp_path: Path) -> None:
    from personal_os_worker.jobs.model_errors import ModelError

    api = Api({"i1": {"status": "open"}})
    entered, release = threading.Event(), threading.Event()

    def runner(_c: list[str], timeout: float = 0) -> str:
        entered.set()
        release.wait(timeout=30)
        raise ModelError("the run ended")

    queue = _queue(api, tmp_path, tmp_path, runner=runner,
                   prober=lambda _r, _a: {"ok": True})
    try:
        problem = _problem()
        assert queue.submit(problem)
        assert entered.wait(timeout=30)
        queue.close()
        release.set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and queue._busy:
            time.sleep(0.02)
        assert api.issues["i1"]["status"] == "open"
        assert api.issues["i1"]["verification_notes"] == (
            "attempts 1 of 3; the run ended before its fix finished"
        )
    finally:
        release.set()
        queue.close()
