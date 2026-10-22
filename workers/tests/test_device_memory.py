import signal
from pathlib import Path

from personal_os_worker.device.memory import MemoryWatch, Process, read_processes, tree

AGENT = 100
GB = 1024**3


def status(name: str, ppid: int, rss_kb: int | None) -> str:
    lines = [f"Name:\t{name}", "State:\tS (sleeping)", f"PPid:\t{ppid}"]
    if rss_kb is not None:
        lines.append(f"VmRSS:\t  {rss_kb} kB")
    return "\n".join(lines) + "\n"


def test_proc_reads_as_parent_name_and_resident_bytes(tmp_path: Path) -> None:
    for pid, text in {
        "100": status("python3", 1, 2048),
        "2": status("kthreadd", 0, None),
    }.items():
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "status").write_text(text)
    (tmp_path / "self").mkdir()
    (tmp_path / "300").mkdir()  # exited before its status was read

    assert read_processes(tmp_path) == {
        100: Process(100, 1, "python3", 2048 * 1024),
        2: Process(2, 0, "kthreadd", 0),
    }


PROCESSES = {
    1: Process(1, 0, "init", 10),
    AGENT: Process(AGENT, 1, "python3", 300),
    101: Process(101, AGENT, "python3", 900),  # a page run
    102: Process(102, 101, "MainThread", 200),  # its Playwright driver
    200: Process(200, 1, "bash", 5),
    201: Process(201, 200, "os", 700),  # a terminal run
    300: Process(300, 1, "claude", 5000),  # unrelated
}


def test_the_watched_tree_is_the_agent_its_children_and_the_linked_run() -> None:
    assert [process.pid for process in tree(PROCESSES, [AGENT, 201])] == [101, 201, AGENT, 102]
    assert [process.pid for process in tree(PROCESSES, [AGENT, 999])] == [101, AGENT, 102]


def watch(tmp_path: Path, processes: dict[int, Process], killed: list[tuple[int, int]],
          limit: int = 800) -> MemoryWatch:
    return MemoryWatch(tmp_path / "memory.log", limit=limit, read=lambda: processes,
                       kill=lambda pid, sig: killed.append((pid, sig)), agent_pid=AGENT)


def test_every_check_records_a_line_and_stops_nothing_under_the_limit(tmp_path: Path) -> None:
    killed: list[tuple[int, int]] = []
    assert watch(tmp_path, PROCESSES, killed, limit=GB).check() is None
    assert killed == []
    line = (tmp_path / "memory.log").read_text()
    assert "in 3 processes: python3:101=" in line
    assert "claude" not in line


def test_the_largest_process_past_the_limit_is_stopped(tmp_path: Path) -> None:
    killed: list[tuple[int, int]] = []
    over = watch(tmp_path, PROCESSES, killed).check(run_pid=201)
    assert over is not None and over.pid == 101
    assert killed == [(101, signal.SIGTERM)]
    assert "stopped python3:101" in (tmp_path / "memory.log").read_text()


def test_the_agent_is_never_signalled_only_returned(tmp_path: Path) -> None:
    killed: list[tuple[int, int]] = []
    over = watch(tmp_path, PROCESSES, killed, limit=250).check()
    assert over is not None and over.pid == 101
    big_agent = PROCESSES | {AGENT: Process(AGENT, 1, "python3", 5000)}
    over = watch(tmp_path, big_agent, killed := []).check()
    assert over is not None and over.pid == AGENT
    assert killed == []
    assert "the agent is past" in (tmp_path / "memory.log").read_text()


def test_a_process_that_already_exited_is_not_an_error(tmp_path: Path) -> None:
    def gone(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid)

    made = MemoryWatch(tmp_path / "memory.log", limit=800, read=lambda: PROCESSES, kill=gone,
                       agent_pid=AGENT)
    assert made.check() is not None
