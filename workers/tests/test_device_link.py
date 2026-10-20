import io
import json
import socket
import threading
from pathlib import Path
from typing import Any

from rich.console import Console

from personal_os_worker.device import link


def rendered(markup: str) -> str:
    buffer = io.StringIO()
    Console(file=buffer, force_terminal=True, color_system="standard", width=120).print(markup)
    return buffer.getvalue().rstrip("\n")


def test_ansi_lines_become_styled_spans_the_page_can_map_to_tokens() -> None:
    spans = link.ansi_spans(
        rendered("  [green]pattern[/green] Name -> [bold]Ethan[/bold] [yellow]Warning[/yellow]")
    )
    assert "".join(span["text"] for span in spans) == "  pattern Name -> Ethan Warning"
    styled = {span["text"]: span.get("style", []) for span in spans}
    assert styled["pattern"] == ["green"]
    assert styled["Ethan"] == ["bold"]
    assert styled["Warning"] == ["yellow"]
    assert all("\x1b" not in span["text"] for span in spans)


def test_dim_red_and_links_survive() -> None:
    spans = link.ansi_spans(rendered("[dim]quiet[/dim] [bold red]bad[/bold red] [link=https://jobs.example/1]Role[/link]"))
    by_text = {span["text"]: span for span in spans}
    assert by_text["quiet"]["style"] == ["dim"]
    assert by_text["bad"]["style"] == ["bold", "red"]
    assert by_text["Role"]["link"] == "https://jobs.example/1"


def test_a_plain_line_is_one_unstyled_span() -> None:
    assert link.ansi_spans("Starting Chrome ...") == [{"text": "Starting Chrome ..."}]
    assert link.ansi_spans("") == []


def test_a_link_to_anything_but_the_web_is_dropped() -> None:
    spans = link.ansi_spans(rendered("[link=javascript:alert(1)]x[/link]"))
    assert spans == [{"text": "x"}]


def serve_once(
    path: Path, reply: dict[str, Any], received: list[dict[str, Any]], *, chat: bool = True
) -> threading.Thread:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    def run() -> None:
        connection, _ = server.accept()
        stream = connection.makefile("rwb")
        received.append(json.loads(stream.readline()))
        stream.write(link.encode(reply))
        stream.flush()
        if reply.get("type") == "attached" and chat:
            stream.write(link.encode({"type": "control", "action": "save"}))
            stream.flush()
            received.append(json.loads(stream.readline()))
        connection.close()
        server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_with_no_agent_listening_there_is_no_link(tmp_path: Path) -> None:
    assert link.LinkClient.connect({"type": "hello"}, path=tmp_path / "device.sock") is None


def test_a_run_says_hello_and_hears_the_agent(tmp_path: Path) -> None:
    path = tmp_path / "device.sock"
    received: list[dict[str, Any]] = []
    thread = serve_once(path, {"type": "attached"}, received)
    client = link.LinkClient.connect({"type": "hello", "run_id": "r1"}, path=path)
    assert client is not None
    messages: list[dict[str, Any]] = []
    done = threading.Event()
    client.start(lambda message: (messages.append(message), done.set()))
    assert done.wait(5)
    client.send({"type": "log", "lines": [[{"text": "hi"}]]})
    thread.join(5)
    client.close()
    assert received[0] == {"type": "hello", "run_id": "r1"}
    assert messages == [{"type": "control", "action": "save"}]
    assert received[1]["type"] == "log"


def test_a_busy_agent_turns_the_run_away(tmp_path: Path) -> None:
    path = tmp_path / "device.sock"
    received: list[dict[str, Any]] = []
    thread = serve_once(path, {"type": "busy"}, received)
    assert link.LinkClient.connect({"type": "hello"}, path=path) is None
    thread.join(5)


def test_sending_after_the_agent_left_is_harmless(tmp_path: Path) -> None:
    path = tmp_path / "device.sock"
    received: list[dict[str, Any]] = []
    thread = serve_once(path, {"type": "attached"}, received, chat=False)
    client = link.LinkClient.connect({"type": "hello"}, path=path)
    assert client is not None
    client.start(lambda message: None)
    thread.join(5)
    assert not thread.is_alive()
    for _ in range(3):
        client.send({"type": "log", "lines": []})
    client.close()
