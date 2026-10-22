"""Watch the memory the agent and its run use, so one leak cannot take WSL down.

Every beat the agent reads, from /proc, the resident memory of itself, every
process under it (a page run, its Playwright driver, a fix's gates), and a run a
terminal linked, and appends one line to `memory.log`. A process past the limit
is stopped and the log names it; the agent itself past it restarts between runs.
Without this, a process that grows until the kernel's OOM killer runs leaves no
record of what it was, and on WSL the kill can stop every shell with it.
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

LIMIT_BYTES = 4 * 1024**3
LOG_LIMIT = 5_000_000
# The largest processes each line names.
SHOWN = 4


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    name: str
    rss: int  # bytes


def read_processes(proc: Path = Path("/proc")) -> dict[int, Process]:
    """Every process's parent, name, and resident memory, as /proc has them now."""
    found: dict[int, Process] = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text()
        except OSError:  # it exited while it was being read
            continue
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        rss = (fields.get("VmRSS") or "0 kB").split()[0]  # a kernel thread has none
        found[int(entry.name)] = Process(
            pid=int(entry.name),
            ppid=int(fields.get("PPid", "0")),
            name=fields.get("Name", "?").strip(),
            rss=int(rss) * 1024,
        )
    return found


def tree(processes: dict[int, Process], roots: Iterable[int]) -> list[Process]:
    """The roots and everything under them, largest first."""
    children: dict[int, list[int]] = {}
    for process in processes.values():
        children.setdefault(process.ppid, []).append(process.pid)
    seen: set[int] = set()
    stack = [pid for pid in roots if pid in processes]
    while stack:
        pid = stack.pop()
        if pid not in seen:
            seen.add(pid)
            stack.extend(children.get(pid, []))
    return sorted((processes[pid] for pid in seen), key=lambda process: process.rss, reverse=True)


def gigabytes(size: int) -> str:
    return f"{size / 1024**3:.2f}G"


def describe(watched: list[Process]) -> str:
    total = sum(process.rss for process in watched)
    largest = " ".join(
        f"{process.name}:{process.pid}={gigabytes(process.rss)}" for process in watched[:SHOWN]
    )
    return f"{gigabytes(total)} in {len(watched)} processes: {largest}"


class MemoryWatch:
    def __init__(
        self,
        path: Path,
        *,
        limit: int = LIMIT_BYTES,
        read: Callable[[], dict[int, Process]] = read_processes,
        kill: Callable[[int, int], None] = os.kill,
        agent_pid: int | None = None,
    ) -> None:
        self.path = path
        self.limit = limit
        self.read = read
        self.kill = kill
        self.agent_pid = os.getpid() if agent_pid is None else agent_pid

    def check(self, run_pid: int | None = None) -> Process | None:
        """Record one sample; stop and return the largest process past the limit.

        The agent is never signalled here: it is returned, and restarts itself
        once no run is active.
        """
        roots = [self.agent_pid, *([run_pid] if run_pid else [])]
        watched = tree(self.read(), roots)
        self.write(describe(watched))
        over = next((process for process in watched if process.rss > self.limit), None)
        if over is None:
            return None
        if over.pid == self.agent_pid:
            self.write(f"the agent is past the {gigabytes(self.limit)} limit; "
                       "it restarts once no run is active")
            return over
        with suppress(ProcessLookupError):
            self.kill(over.pid, signal.SIGTERM)
        self.write(f"stopped {over.name}:{over.pid} at {gigabytes(over.rss)}, "
                   f"past the {gigabytes(self.limit)} limit")
        return over

    def write(self, line: str) -> None:
        with suppress(OSError):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > LOG_LIMIT:
                self.path.replace(self.path.with_suffix(".log.1"))
            with self.path.open("a") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
