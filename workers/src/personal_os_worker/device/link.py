"""The socket between an apply run and this device's agent.

JSON lines over a Unix socket in `state_dir()`. The agent listens; an apply run
connects when it starts, says hello, and hears back `attached` or `busy`. With
no agent listening, `connect` returns None and the run behaves exactly as it
always has. A run the agent started itself carries its handle in
`PERSONAL_OS_AGENT_RUN`, so the agent can tell its own run from a terminal one.

The run's console lines cross as styled spans, so the page can map them to its
own tokens and never sees a raw color: `ansi_spans` is that conversion.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.style import Style
from rich.text import Text

from personal_os_worker.core.service import state_dir

AGENT_RUN_ENV = "PERSONAL_OS_AGENT_RUN"
HANDSHAKE_SECONDS = 5.0
# The eight ANSI colors by number; the bright eight map onto the same names.
COLORS = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
FLAGS = ("bold", "dim", "italic", "underline")

_RENDERER = Console(color_system=None, width=10_000)


def socket_path() -> Path:
    return state_dir() / "device.sock"


def encode(message: dict[str, Any]) -> bytes:
    return json.dumps(message, separators=(",", ":"), default=str).encode() + b"\n"


def _style_names(style: Style) -> list[str]:
    names = [flag for flag in FLAGS if getattr(style, flag)]
    color = style.color
    if color is not None and color.number is not None and color.number < 16:
        names.append(COLORS[color.number % 8])
    return names


def ansi_spans(line: str) -> list[dict[str, Any]]:
    """One console line as `{"text", "style"?, "link"?}` spans, escapes removed."""
    spans: list[dict[str, Any]] = []
    for segment in Text.from_ansi(line).render(_RENDERER, end=""):
        if not segment.text or segment.control:
            continue
        span: dict[str, Any] = {"text": segment.text}
        style = segment.style or Style()
        names = _style_names(style)
        if names:
            span["style"] = names
        if style.link and style.link.startswith(("https://", "http://")):
            span["link"] = style.link
        previous = spans[-1] if spans else None
        if previous is not None and {k: v for k, v in previous.items() if k != "text"} == {
            k: v for k, v in span.items() if k != "text"
        }:
            previous["text"] += span["text"]
        else:
            spans.append(span)
    return spans


class LinkClient:
    """The apply run's end of the socket: send freely, hear the agent on a thread."""

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection
        self._reader = connection.makefile("rb")
        self._lock = threading.Lock()
        self.closed = False

    @classmethod
    def connect(
        cls, hello: dict[str, Any], *, path: Path | None = None
    ) -> LinkClient | None:
        """Say hello to the agent; None when none listens or it is busy with another run."""
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(HANDSHAKE_SECONDS)
            connection.connect(str(path or socket_path()))
            connection.sendall(encode(hello))
            client = cls(connection)
            reply = json.loads(client._reader.readline() or b"{}")
        except (OSError, ValueError):
            connection.close()
            return None
        if reply.get("type") != "attached":
            client.close()
            return None
        connection.settimeout(None)
        return client

    def start(self, on_message: Callable[[dict[str, Any]], None]) -> None:
        """Hand every message from the agent to `on_message`, on a daemon thread."""

        def read() -> None:
            try:
                for raw in self._reader:
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(message, dict):
                        on_message(message)
            except (OSError, ValueError):
                pass
            self.closed = True

        threading.Thread(target=read, name="device-link", daemon=True).start()

    def send(self, message: dict[str, Any]) -> None:
        """Best effort: a run never stops because the agent went away."""
        if self.closed:
            return
        with self._lock:
            try:
                self._connection.sendall(encode(message))
            except OSError:
                self.closed = True

    def close(self) -> None:
        self.closed = True
        with contextlib.suppress(OSError):
            self._connection.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self._reader.close()
        with contextlib.suppress(OSError):
            self._connection.close()
