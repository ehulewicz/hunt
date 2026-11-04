import asyncio
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.device import runs
from personal_os_worker.device.link import LinkClient
from personal_os_worker.device.runs import RunBusy, RunManager, apply_command

FAKE_RUN = textwrap.dedent(
    """
    import json, os, socket, sys, time
    print("\\x1b[2mStarting the local API ...\\x1b[0m", flush=True)
    time.sleep(0.3)
    s = socket.socket(socket.AF_UNIX)
    s.connect(sys.argv[1])
    f = s.makefile("rwb")
    def send(message):
        f.write((json.dumps(message) + "\\n").encode())
        f.flush()
    send({"type": "hello", "agent_run": os.environ["PERSONAL_OS_AGENT_RUN"], "run_id": "r1",
          "company": "Acme", "title": "Intern", "remote": True})
    assert json.loads(f.readline())["type"] == "attached"
    print("on the socket now, stdout is not the log", flush=True)
    send({"type": "log", "lines": [[{"text": "filled"}]]})
    send({"type": "gaps", "items": [{"question": "GPA", "cause": "fact_gap"}]})
    send({"type": "letter", "text": "Dear Acme"})
    letter = json.loads(f.readline())
    fact = json.loads(f.readline())
    control = json.loads(f.readline())
    send({"type": "fact_result", "question": fact["question"], "ok": True})
    send({"type": "log", "lines": [[{"text": letter["text"] + "|" + fact["value"] + "|"
          + control["action"]}]]})
    send({"type": "end", "outcome": "submitted"})
    """
)


class Agent:
    """A run manager on a real socket, recording what it would tell the page."""

    def __init__(self, tmp_path: Path) -> None:
        self.socket = tmp_path / "device.sock"
        self.published: list[dict[str, Any]] = []
        self.updates: list[str] = []
        self.tabs: list[str] = []
        self.script = tmp_path / "fake_run.py"
        self.script.write_text(FAKE_RUN)

        async def publish(message: dict[str, Any]) -> None:
            self.published.append(message)

        async def on_tab(target: str) -> None:
            self.tabs.append(target)

        def update() -> str:
            self.updates.append("checked")
            return "up to date at 4c1946d"

        self.manager = RunManager(
            publish=publish,
            update=update,
            on_tab=on_tab,
            command=lambda reference, fresh: [sys.executable, str(self.script),
                                              str(self.socket)],
            log_dir=tmp_path / "runs",
        )

    async def __aenter__(self) -> "Agent":
        self.server = await asyncio.start_unix_server(self.manager.handle_link, str(self.socket))
        return self

    async def __aexit__(self, *_: object) -> None:
        self.server.close()

    def text(self) -> list[str]:
        return [
            "".join(span["text"] for span in line)
            for message in self.published if message["type"] == "log"
            for line in message["lines"]
        ]

    async def until(self, check: Any, seconds: float = 10.0) -> None:
        deadline = asyncio.get_running_loop().time() + seconds
        while not check():
            assert asyncio.get_running_loop().time() < deadline, self.published
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("reference", ["-rf", "--dry-run", "a b", "12; rm", "ftp://x", ""])
def test_a_reference_can_never_become_a_flag(reference: str) -> None:
    assert not runs.valid_reference(reference)


@pytest.mark.parametrize(
    "reference", [None, "12", "4c19ab", "4c1946d0-a1b2", "https://boards.greenhouse.io/x/jobs/1"]
)
def test_board_numbers_id_prefixes_and_urls_are_references(reference: str | None) -> None:
    assert runs.valid_reference(reference)


def test_the_run_is_this_checkouts_apply_in_remote_mode() -> None:
    command = apply_command("12", fresh=True)
    assert command[:3] == [sys.executable, "-m", "personal_os_worker"]
    assert command[3:] == ["hunt", "apply", "12", "--remote", "--fresh"]
    assert apply_command(None, fresh=False)[3:] == ["hunt", "apply", "--remote"]


async def test_a_page_run_updates_first_then_reports_and_takes_answers(tmp_path: Path) -> None:
    async with Agent(tmp_path) as agent:
        state = await agent.manager.start("12")
        assert agent.manager.busy()
        await agent.until(lambda: state.letter == "Dear Acme")
        assert agent.updates == ["checked"]
        assert state.company == "Acme" and state.run_id == "r1" and state.remote
        assert agent.manager.active_run_id() == "r1"
        assert state.gaps == [{"question": "GPA", "cause": "fact_gap"}]

        await agent.manager.letter("Edited letter")
        await agent.manager.fact({"question": "GPA", "key": "education.gpa", "value": "3.78"})
        await agent.manager.control("done")
        await agent.until(lambda: not agent.manager.busy())

    assert state.outcome == "submitted"
    assert state.gaps == [] and state.letter is None
    lines = agent.text()
    assert lines[0] == "up to date at 4c1946d"
    assert "Starting the local API ..." in lines  # before the socket, stdout is the log
    assert "on the socket now, stdout is not the log" not in lines
    assert "filled" in lines and "Edited letter|3.78|done" in lines
    assert agent.manager.active_run_id() is None
    assert list((tmp_path / "runs").iterdir())


LINGERING_RUN = textwrap.dedent(
    """
    import json, os, socket, sys, time
    s = socket.socket(socket.AF_UNIX)
    s.connect(sys.argv[1])
    f = s.makefile("rwb")
    def send(message):
        f.write((json.dumps(message) + "\\n").encode())
        f.flush()
    send({"type": "hello", "agent_run": os.environ["PERSONAL_OS_AGENT_RUN"], "run_id": "r1"})
    assert json.loads(f.readline())["type"] == "attached"
    send({"type": "end", "outcome": "skipped"})
    time.sleep(0.5)  # closing the browser and flushing, after it said it ended
    """
)


async def test_a_run_that_said_it_ended_is_busy_until_its_process_is_reaped(
    tmp_path: Path,
) -> None:
    """Idle means the agent may restart itself, and a restart before the wait
    leaves the run's process a zombie for as long as the agent lives."""
    async with Agent(tmp_path) as agent:
        agent.script.write_text(LINGERING_RUN)
        state = await agent.manager.start("12")
        await agent.until(lambda: state.outcome == "skipped")
        assert agent.manager.busy() and agent.manager.active_run_id() is None
        await agent.until(lambda: not agent.manager.busy())


async def test_a_second_run_waits_for_the_first(tmp_path: Path) -> None:
    async with Agent(tmp_path) as agent:
        state = await agent.manager.start(None)
        with pytest.raises(RunBusy):
            await agent.manager.start("3")
        # Stop before the run is even spawned: it never starts.
        await agent.manager.control("stop")
        await agent.until(lambda: not agent.manager.busy())
    assert state.outcome == "stopped"
    assert "Starting the local API ..." not in agent.text()


async def test_a_terminal_run_registers_and_a_second_is_turned_away(tmp_path: Path) -> None:
    async with Agent(tmp_path) as agent:
        first = await asyncio.to_thread(
            LinkClient.connect,
            {"type": "hello", "run_id": "t1", "company": "Beta", "title": "SWE", "pid": 4242},
            path=agent.socket,
        )
        assert first is not None
        await agent.until(lambda: agent.manager.busy())
        assert agent.manager.current is not None
        assert agent.manager.current.origin == "terminal"
        assert agent.manager.current.company == "Beta"
        # The memory watch follows a terminal run by the pid its hello gave.
        await agent.until(lambda: agent.manager.active_pid() == 4242)
        await agent.manager.note("Stopped python3 (4242)")
        assert agent.text()[-1] == "Stopped python3 (4242)"

        second = await asyncio.to_thread(
            LinkClient.connect, {"type": "hello", "run_id": "t2"}, path=agent.socket
        )
        assert second is None

        received: list[dict[str, Any]] = []
        first.start(received.append)
        first.send({"type": "tab", "target": "T7"})
        await agent.until(lambda: agent.tabs == ["T7"])
        await agent.manager.control("skip")
        await agent.until(lambda: received == [{"type": "control", "action": "skip"}])

        first.close()
        await agent.until(lambda: not agent.manager.busy())
        assert agent.manager.current.outcome == "disconnected"


async def test_a_page_run_that_dies_before_the_socket_is_still_reported(tmp_path: Path) -> None:
    async with Agent(tmp_path) as agent:
        agent.script.write_text("import sys\nprint('Nothing listed on the board.')\nsys.exit(1)\n")
        state = await agent.manager.start(None)
        await agent.until(lambda: not agent.manager.busy())
    assert state.outcome == "failed"
    assert "Nothing listed on the board." in agent.text()


async def test_a_direction_goes_to_the_run_and_an_empty_one_does_not(tmp_path: Path) -> None:
    manager = RunManager(publish=_no_publish, update=lambda: "", log_dir=tmp_path)
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> bool:
        sent.append(message)
        return True

    manager._send = send  # type: ignore[method-assign]
    await manager.guide("k", "   ")
    await manager.guide("k", "x" * 3000)
    assert sent == [{"type": "guide", "key": "k", "text": "x" * 2000}]


async def _no_publish(_message: dict[str, Any]) -> None:
    return None
