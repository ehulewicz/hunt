"""An apply run's line to this device's agent, so the Hunt page can see and drive it.

When an agent is listening, `os hunt apply` registers with it: its console is
mirrored line by line as styled spans, it reports the tab it is on, and the
audit's gaps go to the page as structured items. The page's Save, Submitted,
and Skip are the only way to press them, and Stop is Ctrl+C. A run the page started
(`--remote`) also sends its drafted cover letter to the page and waits for the
edited text; a terminal run keeps VS Code for that.
"""

from __future__ import annotations

import _thread
import contextlib
import io
import os
import queue
import re
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import TracebackType
from typing import IO, Any, cast

from playwright.sync_api import Error, Page

from personal_os_worker.core.config import console
from personal_os_worker.device.link import AGENT_RUN_ENV, LinkClient, ansi_spans, socket_path
from personal_os_worker.jobs.audit import Finding
from personal_os_worker.jobs.facts.catalog import entry
from personal_os_worker.jobs.facts.derived import read_only_refusal
from personal_os_worker.jobs.keys import NoKeys, Terminal

FACT_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
PAGE_CONTROLS = frozenset({"save", "done", "skip"})
LETTER_POLL_SECONDS = 0.5
# The agent's answers to a request (`RunLink.request`), as the device press sends them.
REPLIES = frozenset({"press_ready", "press_outcome"})


class ConsoleTee(io.TextIOBase):
    """The console's file: everything still reaches the terminal, each line the page too."""

    def __init__(self, original: IO[str], emit: Callable[[list[dict[str, Any]]], None]) -> None:
        super().__init__()
        self._original = original
        self._emit = emit
        self._pending = ""

    def write(self, text: str) -> int:
        self._original.write(text)
        *lines, self._pending = (self._pending + text).split("\n")
        for line in lines:
            self._emit(ansi_spans(line))
        return len(text)

    def flush(self) -> None:
        self._original.flush()

    def isatty(self) -> bool:
        return self._original.isatty()

    def fileno(self) -> int:
        return self._original.fileno()

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return str(getattr(self._original, "encoding", None) or "utf-8")


def gap_items(findings: list[Finding]) -> list[dict[str, Any]]:
    """The audit's empty questions for the page; a fact gap it can fill has an input.

    A gap is settable the way `--ask-facts` would set it: a stored fact, named
    or not yet named. A read-only fact (a record fact or a computed one) is
    never asked for here.
    """
    items: list[dict[str, Any]] = []
    for finding in findings:
        if finding.cause == "wrong_answer":
            continue
        key = finding.fact_key
        if key is None and finding.missing_fact and FACT_KEY.fullmatch(finding.missing_fact):
            key = finding.missing_fact
        known = entry(key) if key else None
        refusal = read_only_refusal(key) if key else None
        item: dict[str, Any] = {
            "question": finding.question,
            "cause": finding.cause,
            "required": finding.required,
            "note": finding.note,
            "fact_key": key,
            "missing_fact": finding.missing_fact,
            "label": known.label if known else None,
            "value_type": known.value_type if known else None,
            "settable": finding.cause == "fact_gap" and refusal is None,
        }
        if refusal is not None:
            item["hint"] = refusal
        items.append(item)
    return items


class RunLink:
    """This run's registration with the agent, and what the page sent back."""

    def __init__(self, client: LinkClient, *, interrupt: Callable[[], None] | None = None) -> None:
        self._client = client
        self._interrupt = interrupt or _thread.interrupt_main
        self._lock = threading.Lock()
        self._controls: set[str] = set()
        self._facts: list[dict[str, Any]] = []
        self._letters: queue.Queue[str] = queue.Queue()
        self._tab: str | None = None
        self._targets: dict[Any, str] = {}
        self._guide: Callable[[str, str | None], str] | None = None
        self._replies: queue.Queue[dict[str, Any]] = queue.Queue()
        client.start(self._receive)

    @classmethod
    def connect(
        cls, *, run_id: str, company: str, title: str, remote: bool
    ) -> RunLink | None:
        """None when no agent listens, or it is busy with another run."""
        client = LinkClient.connect(
            {
                "type": "hello",
                "agent_run": os.environ.get(AGENT_RUN_ENV),
                "run_id": run_id,
                "company": company,
                "title": title,
                "remote": remote,
                "pid": os.getpid(),
            },
            path=socket_path(),
        )
        return cls(client) if client is not None else None

    def _receive(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "control":
            action = str(message.get("action") or "")
            if action == "stop":
                # The same KeyboardInterrupt path as Ctrl+C at the terminal.
                self._interrupt()
            elif action in PAGE_CONTROLS:
                with self._lock:
                    self._controls.add(action)
        elif kind == "letter":
            self._letters.put(str(message.get("text") or ""))
        elif kind == "fact":
            with self._lock:
                self._facts.append(message)
        elif kind in REPLIES:
            self._replies.put(message)
        elif kind == "guide":
            answer = "The fixer is off in this run, so the direction was not sent."
            if self._guide is not None:
                key = str(message.get("key") or "") or None
                answer = self._guide(str(message.get("text") or ""), key)
            console.print(f"  [dim]{answer}[/dim]")

    def on_guide(self, guide: Callable[[str, str | None], str]) -> None:
        """Where the page's directions for the fixer go."""
        self._guide = guide

    @property
    def closed(self) -> bool:
        return bool(self._client.closed)

    def controls(self) -> set[str]:
        with self._lock:
            pressed, self._controls = self._controls, set()
        return pressed

    def fact_answers(self) -> list[dict[str, Any]]:
        with self._lock:
            answers, self._facts = self._facts, []
        return answers

    def log(self, spans: list[dict[str, Any]]) -> None:
        self._client.send({"type": "log", "lines": [spans]})

    def gaps(self, items: list[dict[str, Any]]) -> None:
        self._client.send({"type": "gaps", "items": items})

    def fixes(self, items: list[dict[str, Any]]) -> None:
        """Where each fix the fixer is working on stands."""
        self._client.send({"type": "fixes", "items": items})

    def reload(self) -> None:
        """This process is about to become one running a kept fix: keep the run's place."""
        self._client.send({"type": "reload"})

    def fact_result(self, question: str, *, ok: bool, message: str) -> None:
        self._client.send(
            {"type": "fact_result", "question": question, "ok": ok, "message": message}
        )

    def _target_of(self, page: Page) -> str | None:
        """The CDP target id of a Playwright page, which is how the agent names tabs."""
        if page not in self._targets:
            try:
                session = page.context.new_cdp_session(page)
                try:
                    info = session.send("Target.getTargetInfo")
                finally:
                    session.detach()
                self._targets[page] = str(info["targetInfo"]["targetId"])
            except (Error, KeyError, TypeError):
                return None
        return self._targets[page]

    def tab(self, page: Page) -> None:
        """Tell the agent which tab the run is on, when that changes."""
        target = self._target_of(page)
        if target is not None and target != self._tab:
            self._tab = target
            self._client.send({"type": "tab", "target": target})

    def edit_letter(self, path: Path) -> bool:
        """The Hunt page's letter review: it edits the draft, the file gets the result.

        True when the page answered; False when the agent went away first,
        which the caller treats as "not reviewed".
        """
        self._client.send({"type": "letter", "text": path.read_text(encoding="utf-8")})
        while True:
            try:
                text = self._letters.get(timeout=LETTER_POLL_SECONDS)
            except queue.Empty:
                if self.closed:
                    return False
                continue
            path.write_text(text, encoding="utf-8")
            return True

    def request(
        self, message: dict[str, Any], reply: str, *, timeout: float
    ) -> dict[str, Any] | None:
        """Send `message` and wait for the agent's `reply`; None on a timeout or a lost link."""
        while True:  # a late reply to an earlier request is not this one's
            try:
                self._replies.get_nowait()
            except queue.Empty:
                break
        self._client.send(message)
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            try:
                answer = self._replies.get(timeout=min(left, LETTER_POLL_SECONDS))
            except queue.Empty:
                if self.closed:
                    return None
                continue
            if answer.get("type") == reply:
                return answer
        return None

    def end(self, outcome: str) -> None:
        self._client.send({"type": "end", "outcome": outcome})

    def close(self) -> None:
        self._client.close()


class LinkedKeys:
    """The terminal held for the run, if there is one, and the page's buttons."""

    def __init__(self, link: RunLink, terminal: Terminal | NoKeys) -> None:
        self.link = link
        self.terminal = terminal

    def __enter__(self) -> LinkedKeys:
        self.terminal.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.terminal.__exit__(exc_type, exc, tb)

    def wait(self, seconds: float) -> set[str]:
        return self.terminal.wait(seconds) | self.link.controls()


@contextlib.contextmanager
def mirrored(link: RunLink) -> Iterator[None]:
    """Mirror the console to the page for as long as the run lasts."""
    previous = console._file
    console.file = cast(IO[str], ConsoleTee(console.file, link.log))
    try:
        yield
    finally:
        console._file = previous
