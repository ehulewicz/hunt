"""The apply runs this device's agent knows about: one at a time.

A run the page starts is `os hunt apply <ref> --remote`, spawned after the
checkout is brought current. A run started in a terminal finds the agent's
socket and registers, so the page sees it and its controls work there too. Both
report over the same socket: console lines as styled spans, the tab they are on,
a drafted cover letter, and the gaps the audit left. The page answers with
controls, the edited letter, and facts.

Until a page-started run says hello on the socket, its own output is the log, so
an early failure (a bad reference, a closed posting) still reaches the page.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import sys
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personal_os_worker.device.link import AGENT_RUN_ENV, ansi_spans, encode

LOG_LINES = 3000
# A board number, an id prefix, or a posting URL. Never anything starting with
# "-": the reference becomes an argument, and must not become a flag.
REFERENCE = re.compile(r"^(\d{1,6}|[0-9a-fA-F][0-9a-fA-F-]{3,35}|https?://\S{1,2000})$")
CONTROLS = frozenset({"save", "done", "skip", "stop"})

Publish = Callable[[dict[str, Any]], Awaitable[None]]


class RunBusy(RuntimeError):
    """A run is already in progress on this device."""


def valid_reference(reference: str | None) -> bool:
    return reference is None or REFERENCE.fullmatch(reference) is not None


def apply_command(reference: str | None, *, fresh: bool) -> list[str]:
    """`os hunt apply` as this interpreter runs it, so it is this checkout's code."""
    return [
        sys.executable, "-m", "personal_os_worker", "hunt", "apply",
        *([reference] if reference else []),
        "--remote",
        *(["--fresh"] if fresh else []),
    ]


def run_environment(key: str) -> dict[str, str]:
    """The run's environment: its handle, and a console that keeps its colors."""
    environment = dict(os.environ)
    environment.update(
        {
            AGENT_RUN_ENV: key,
            "FORCE_COLOR": "1",
            "TERM": "xterm-256color",
            "COLUMNS": "110",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


@dataclass
class RunState:
    key: str
    origin: str  # "page" or "terminal"
    reference: str | None = None
    phase: str = "updating"  # updating, starting, running, ended
    note: str | None = None
    run_id: str | None = None
    company: str | None = None
    title: str | None = None
    remote: bool = False
    outcome: str | None = None
    letter: str | None = None
    gaps: list[dict[str, Any]] = field(default_factory=list)
    tab: str | None = None
    linked: bool = False
    stop_requested: bool = False
    started_at: float = field(default_factory=time.time)
    lines: deque[list[dict[str, Any]]] = field(default_factory=lambda: deque(maxlen=LOG_LINES))

    @property
    def active(self) -> bool:
        return self.phase != "ended"

    def snapshot(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "origin": self.origin,
            "reference": self.reference,
            "phase": self.phase,
            "note": self.note,
            "run_id": self.run_id,
            "company": self.company,
            "title": self.title,
            "remote": self.remote,
            "outcome": self.outcome,
            "letter": self.letter,
            "gaps": self.gaps,
            "tab": self.tab,
            "started_at": self.started_at,
        }


class RunManager:
    def __init__(
        self,
        *,
        publish: Publish,
        update: Callable[[], str],
        on_tab: Callable[[str], Awaitable[None]] | None = None,
        command: Callable[[str | None, bool], list[str]] = (
            lambda reference, fresh: apply_command(reference, fresh=fresh)
        ),
        log_dir: Path | None = None,
    ) -> None:
        self._publish = publish
        self._update = update
        self._on_tab = on_tab
        self._command = command
        self._log_dir = log_dir
        self._writer: asyncio.StreamWriter | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self.current: RunState | None = None

    # ----- what the page and the heartbeat ask ------------------------------

    def busy(self) -> bool:
        return self.current is not None and self.current.active

    def active_run_id(self) -> str | None:
        return self.current.run_id if self.current is not None and self.current.active else None

    def snapshot(self) -> dict[str, Any] | None:
        return self.current.snapshot() if self.current is not None else None

    def history(self) -> list[list[dict[str, Any]]]:
        return list(self.current.lines) if self.current is not None else []

    # ----- starting a run from the page --------------------------------------

    async def start(self, reference: str | None, *, fresh: bool = False) -> RunState:
        if not valid_reference(reference):
            raise ValueError("A run takes a board number, an id prefix, or a posting URL")
        if self.busy():
            raise RunBusy("A run is already in progress on this device")
        state = RunState(key=str(uuid.uuid4()), origin="page", reference=reference, remote=True)
        self.current = state
        await self._publish_run()
        task = asyncio.create_task(self._launch(state, fresh=fresh))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return state

    async def _launch(self, state: RunState, *, fresh: bool) -> None:
        try:
            state.note = await asyncio.to_thread(self._update)
        except Exception as error:  # an update that breaks still leaves the checkout runnable
            state.note = f"update failed: {error}"
        await self._line(state, [{"text": state.note or "", "style": ["dim"]}])
        if state.stop_requested:
            await self._end(state, "stopped")
            return
        state.phase = "starting"
        await self._publish_run()
        log = self._log_file(state)
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command(state.reference, fresh),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=run_environment(state.key),
                cwd=str(Path.home()),
            )
        except OSError as error:
            await self._line(state, [{"text": f"Could not start the run: {error}",
                                      "style": ["red"]}])
            await self._end(state, "failed")
            return
        self._process = process
        assert process.stdout is not None
        async for raw in process.stdout:
            text = raw.decode(errors="replace").rstrip("\n")
            if log is not None:
                with contextlib.suppress(OSError):
                    log.write(text + "\n")
                    log.flush()
            # Once the run is on the socket its console arrives there, styled.
            if not state.linked:
                await self._line(state, ansi_spans(text))
        code = await process.wait()
        self._process = None
        if log is not None:
            log.close()
        if state.active:
            await self._end(state, "failed" if code else "stopped")

    def _log_file(self, state: RunState) -> Any:
        if self._log_dir is None:
            return None
        with contextlib.suppress(OSError):
            self._log_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            return (self._log_dir / f"{stamp}-{state.key[:8]}.log").open("a")
        return None

    # ----- the socket an apply run reports through ----------------------------

    async def handle_link(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = json.loads(await asyncio.wait_for(reader.readline(), 10) or b"{}")
        except (TimeoutError, ValueError):
            writer.close()
            return
        state = self._claim(hello)
        if state is None:
            writer.write(encode({"type": "busy"}))
            with contextlib.suppress(OSError):
                await writer.drain()
            writer.close()
            return
        state.run_id = str(hello.get("run_id") or "") or None
        state.company = str(hello.get("company") or "") or None
        state.title = str(hello.get("title") or "") or None
        state.remote = bool(hello.get("remote"))
        state.phase, state.linked = "running", True
        self._writer = writer
        writer.write(encode({"type": "attached"}))
        await writer.drain()
        await self._publish_run()
        try:
            while line := await reader.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    await self._from_run(state, message)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if self._writer is writer:
                self._writer = None
            writer.close()
            # A page run ends when its process does; a terminal run ends here.
            if state.active and state.origin == "terminal":
                await self._end(state, state.outcome or "disconnected")

    def _claim(self, hello: dict[str, Any]) -> RunState | None:
        """The state this connection reports for: the page's own run, or a new terminal run."""
        current = self.current
        handle = str(hello.get("agent_run") or "")
        if current is not None and current.active:
            if handle and handle == current.key and not current.linked:
                return current
            return None
        state = RunState(key=str(uuid.uuid4()), origin="terminal", phase="starting")
        self.current = state
        return state

    async def _from_run(self, state: RunState, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "log":
            for spans in message.get("lines") or []:
                state.lines.append(spans)
            await self._publish({"type": "log", "lines": message.get("lines") or []})
        elif kind == "tab":
            state.tab = str(message.get("target") or "") or None
            if state.tab and self._on_tab is not None:
                with contextlib.suppress(Exception):
                    await self._on_tab(state.tab)
        elif kind == "letter":
            state.letter = str(message.get("text") or "")
            await self._publish({"type": "letter", "text": state.letter})
        elif kind == "gaps":
            state.gaps = [item for item in message.get("items") or [] if isinstance(item, dict)]
            await self._publish({"type": "gaps", "items": state.gaps})
        elif kind == "fact_result":
            if message.get("ok"):
                question = message.get("question")
                state.gaps = [item for item in state.gaps if item.get("question") != question]
            await self._publish(message)
        elif kind == "end":
            await self._end(state, str(message.get("outcome") or "stopped"))

    async def _line(self, state: RunState, spans: list[dict[str, Any]]) -> None:
        state.lines.append(spans)
        await self._publish({"type": "log", "lines": [spans]})

    async def _end(self, state: RunState, outcome: str) -> None:
        state.phase, state.outcome = "ended", outcome
        state.letter, state.gaps = None, []
        await self._publish_run()

    async def _publish_run(self) -> None:
        await self._publish({"type": "run", "run": self.snapshot()})

    # ----- what the page sends to the run ------------------------------------

    async def _send(self, message: dict[str, Any]) -> bool:
        writer = self._writer
        if writer is None:
            return False
        try:
            writer.write(encode(message))
            await writer.drain()
        except (ConnectionError, OSError):
            return False
        return True

    async def control(self, action: str) -> None:
        if action not in CONTROLS or not self.busy():
            return
        if await self._send({"type": "control", "action": action}):
            return
        # Not on the socket yet (still updating or starting): only stop means anything.
        if action != "stop" or self.current is None:
            return
        self.current.stop_requested = True
        if self._process is not None:
            with contextlib.suppress(ProcessLookupError):
                self._process.send_signal(signal.SIGINT)

    async def letter(self, text: str) -> None:
        if self.current is None or self.current.letter is None:
            return
        if await self._send({"type": "letter", "text": text}):
            self.current.letter = None
            await self._publish_run()

    async def fact(self, answer: dict[str, Any]) -> None:
        fields = ("question", "key", "label", "value_type", "value")
        await self._send({"type": "fact", **{name: answer.get(name) for name in fields}})
