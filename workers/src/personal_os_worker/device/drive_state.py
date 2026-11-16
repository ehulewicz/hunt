"""What the drive channel keeps on disk: its switch, the per-listing locks, and the audit log.

All of it lives in `state_dir()/drive/`, beside `files/` (the upload files the
agent writes itself) and, later, `attempts.jsonl`. The switch is off until it
is turned on. A lock is keyed by listing id and by the posting's normalized
URL, so the same posting reached as `/apply`, `/thanks` or with a trailing
slash is one listing. The audit log is one line per driver message and never
holds what was typed. Every JSON file is written whole, to a temporary file
that then replaces it.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from personal_os_worker.core.service import state_dir
from personal_os_worker.device.memory import rotate

# The ATS hosts a lease may be on, and every subdomain of them.
ATS_HOSTS = (
    "jobs.lever.co",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.ashbyhq.com",
)
LOCK_STATES = frozenset({"pending", "submitted", "unconfirmed", "burned"})
# A posting's own pages that are still that posting.
POSTING_TAILS = ("/apply", "/thanks")
# The application statuses that mean it may already have gone in.
APPLIED_OR_LATER = frozenset(
    {"applied", "oa", "oa_completed", "interview", "offer", "rejected"}
)


def drive_dir() -> Path:
    return state_dir() / "drive"


def url_host(url: str) -> str:
    with suppress(ValueError):
        return (urlsplit(url).hostname or "").lower()
    return ""


def is_ats_host(host: str) -> bool:
    return any(host == known or host.endswith("." + known) for known in ATS_HOSTS)


def normalize_posting(url: str) -> str:
    """`host/path` with no scheme, query, fragment, trailing slash, `/apply` or `/thanks`."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    path = parts.path.rstrip("/")
    for tail in POSTING_TAILS:
        if path.lower().endswith(tail):
            path = path[: -len(tail)].rstrip("/")
    return f"{(parts.hostname or '').lower()}{path}"


def is_confirmation(url: str) -> bool:
    """An ATS page that says the application went in (Lever: the posting's `/thanks`)."""
    host = url_host(url)
    path = urlsplit(url).path.rstrip("/").lower()
    return host.endswith("lever.co") and path.endswith("/thanks")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w") as file:
            json.dump(value, file, indent=2)
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class DriveSwitch:
    """`switch.json`: whether this device takes a driver at all; off by default."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "switch.json"

    def on(self) -> bool:
        value = read_json(self.path)
        return isinstance(value, dict) and value.get("on") is True

    def set(self, on: bool) -> None:
        write_json(self.path, {"on": on, "since": now_iso()})


class LockStore:
    """`locks.json`: listings a press may have reached, which nothing drives again."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "locks.json"

    def _read(self) -> dict[str, dict[str, Any]]:
        value = read_json(self.path)
        if not isinstance(value, dict):
            return {}
        return {key: row for key, row in value.items() if isinstance(row, dict)}

    def lock(self, listing_id: str, url: str, state: str, note: str = "") -> None:
        if state not in LOCK_STATES:
            raise ValueError(f"Unknown lock state {state!r}")
        locks = self._read()
        locks[listing_id] = {
            "state": state, "url": normalize_posting(url), "note": note[:500], "at": now_iso(),
        }
        write_json(self.path, locks)

    def state_for(self, listing_id: str | None = None, url: str | None = None) -> str | None:
        """The lock on this listing, or on any listing at this posting URL."""
        locks = self._read()
        if listing_id and listing_id in locks:
            return str(locks[listing_id].get("state"))
        key = normalize_posting(url) if url else ""
        if key:
            for row in locks.values():
                if row.get("url") == key:
                    return str(row.get("state"))
        return None

    def listing_for(self, url: str) -> str | None:
        """The locked listing at this posting URL, if one is."""
        key = normalize_posting(url)
        return next(
            (listing for listing, row in self._read().items() if key and row.get("url") == key),
            None,
        )

    def release(self, listing_id: str) -> None:
        locks = self._read()
        if locks.pop(listing_id, None) is not None:
            write_json(self.path, locks)


def summarize(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """What the audit keeps of a command's parameters: never a typed value."""
    if method == "Input.insertText":
        return {"text_length": len(str(params.get("text") or ""))}
    if method == "Input.dispatchKeyEvent":
        return {
            "event": params.get("type"),
            "key_length": len(str(params.get("key") or "")),
            "text_length": len(str(params.get("text") or "")),
        }
    if method == "Page.navigate":
        return {"url": str(params.get("url") or "")[:2000]}
    if method == "Input.dispatchMouseEvent":
        return {name: params.get(name) for name in ("type", "x", "y", "button") if name in params}
    if method == "Runtime.evaluate":
        return {"expression_length": len(str(params.get("expression") or ""))}
    if method == "DOM.setFileInputFiles":
        return {"files": len(params.get("files") or [])}
    return {"keys": sorted(params)[:12]}


class AuditLog:
    """`audit.jsonl`: one line per driver message, rotated at 5 MB."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "audit.jsonl"

    def write(
        self, listing_id: str | None, what: str, summary: dict[str, Any], outcome: str
    ) -> None:
        line = {"ts": now_iso(), "listing_id": listing_id, "what": what,
                "params": summary, "outcome": outcome}
        with suppress(OSError):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            rotate(self.path)
            with self.path.open("a") as handle:
                handle.write(json.dumps(line, default=str) + "\n")
