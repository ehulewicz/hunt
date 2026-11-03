"""The fix queue's shared records, its attributes, and the cross-mixin surface a mixin sees."""

from __future__ import annotations

import queue as queues
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from personal_os_worker.jobs.fix import lane
from personal_os_worker.jobs.fix.keep import Kept

MAX_ATTEMPTS = 3
BRAKE = 3  # problems in a row that use every attempt without a keep


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


@dataclass(frozen=True)
class Candidate:
    """One checked file the probe may try: its attempt folder, file, and stem."""

    attempt_dir: Path
    learned_file: Path
    stem: str


@dataclass
class Ready:
    """Attempts that passed their checks, waiting to be tried on the live tab."""

    problem: lane.Problem
    answer: lane.Answer
    attempt: int
    candidates: list[Candidate]
    reuse: bool = False
    revised: bool = False
    chosen: Candidate | None = None
    done: threading.Event = field(default_factory=threading.Event)
    probe: dict[str, Any] | None = None
    kept: Kept | None = None


State = Literal["persists", "answered", "gone"]
Recheck = Callable[[lane.Problem], State]


Prober = Callable[[Ready, dict[str, Any]], dict[str, Any]]
Checker = Callable[[Path, Path, lane.Problem], str | None]


class FixBase:
    repo: Path
    workdir: Path
    api: Any
    run_id: str
    report: Callable[[str], None]
    publish: Callable[[list[dict[str, Any]]], None] | None
    runner: lane.Runner
    prober: Prober
    keeper: Callable[..., Kept]
    checker: Checker
    loader: Callable[[Path, str], list[Any]] | None
    statuses: dict[str, Status]
    _taken: dict[str, lane.Problem]
    _recorded: dict[str, str]
    _problems: queues.Queue[lane.Problem | None]
    _ready: Ready | None
    _recheck_pending: tuple[lane.Problem, threading.Event] | None
    _recheck_result: State
    _recheck_seen: bool
    _lock: threading.Lock
    _safe_lock: threading.Lock
    _busy: bool
    _kept: int
    _pending: int
    _consecutive_unsolved: int
    _braked: bool
    model_error: str | None
    _closed: bool
    _settled: threading.Event
    _thread: threading.Thread

    if TYPE_CHECKING:
        def _announce(
            self,
            key: str,
            state: str,
            *,
            attempt: int | None = None,
            note: str | None = None,
        ) -> None: ...
        def _attempts_of(self, problem: lane.Problem) -> int: ...
        def _finish(self, problem: lane.Problem, state: str, note: str) -> None: ...
        def _issue(self, problem: lane.Problem, changes: dict[str, Any]) -> None: ...
        def _record_attempt(self, problem: lane.Problem, attempt: int) -> None: ...
        def _report(self, line: str) -> None: ...
