"""What the drive channel keeps on disk: its switch, the per-listing locks, and the audit log.

All of it lives in `state_dir()/drive/`, beside `files/` (the upload files the
agent writes itself) and `attempts.jsonl`, the scorecard of presses. The
switch is off until it
is turned on. A lock is keyed by listing id and by the posting's normalized
URL, so the same posting reached as `/apply`, `/thanks` or with a trailing
slash is one listing. The audit log is one line per driver message and never
holds what was typed. Every JSON file is written whole, to a temporary file
that then replaces it.

What a press did is one of `OUTCOMES`, read by `classify_outcome` from the
page after it, and `OUTCOME_LOCKS` says what that leaves on the listing.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from personal_os_worker.core.service import state_dir
from personal_os_worker.core.wording import CAPTCHA_REJECTED
from personal_os_worker.device.memory import rotate

# The ATS hosts a lease may be on, and every subdomain of them.
ATS_HOSTS = (
    "jobs.lever.co",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.ashbyhq.com",
)
# The ATS hosts where `os hunt apply --submit` presses Submit through the agent
# (experiment 1) instead of Playwright; Greenhouse and Ashby join once proven.
DEVICE_PRESS_HOSTS = frozenset({"jobs.lever.co"})
LOCK_STATES = frozenset({"pending", "submitted", "unconfirmed", "burned"})
# A posting's own pages that are still that posting.
POSTING_TAILS = ("/apply", "/thanks")
# The application statuses that mean it may already have gone in.
APPLIED_OR_LATER = frozenset(
    {"applied", "oa", "oa_completed", "interview", "offer", "rejected"}
)

OUTCOMES = ("submitted", "captcha_rejected", "challenge_shown", "validation", "unconfirmed")
# The lock each outcome leaves; validation releases it for one fix-and-press.
OUTCOME_LOCKS: dict[str, str | None] = {
    "submitted": "submitted", "captcha_rejected": "burned", "challenge_shown": "burned",
    "validation": None, "unconfirmed": "unconfirmed",
}
CONFIRMED_WORDS = re.compile(
    r"\bapplication\s+(?:submitted|received)\b"
    r"|\bthanks?(?:\s+you)?\s+for\s+(?:applying|your\s+application)\b",
    re.IGNORECASE,
)
# A field error the page shows after a press; only these phrases, so a posting's
# own "is required" never reads as one.
FIELD_ERROR_WORDS = re.compile(
    r"\bthis\s+field\s+is\s+required\b|\bplease\s+fill\s+(?:out|in)\s+this\s+field\b"
    r"|\bplease\s+(?:enter|provide|select)\s+a\s+valid\b"
    r"|\bplease\s+complete\s+(?:this|all)\s+required\s+fields?\b",
    re.IGNORECASE,
)
CHALLENGE_MIN_PX = 100
UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_SUFFIXES = frozenset({".pdf", ".docx"})


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


def is_challenge_frame(src: str, width: float, height: float, visible: bool) -> bool:
    """A visible hCaptcha challenge: its frame says `frame=challenge` and is over 100x100."""
    return (visible and "hcaptcha" in src.lower() and "frame=challenge" in src
            and width > CHALLENGE_MIN_PX and height > CHALLENGE_MIN_PX)


def classify_outcome(
    url: str,
    text: str,
    before_url: str,
    *,
    challenge: bool = False,
    field_errors: int | None = None,
) -> str:
    """What a press did, from the page after it: one of `OUTCOMES`.

    `text` is the body's visible text, `challenge` whether a challenge frame is
    showing (`is_challenge_frame`), and `field_errors` the count of visible field
    errors when the caller read them; without it they are read from `text`. A
    captcha rejection wins over everything, so a refused press is never read as
    gone in; field errors count only while the URL is the one pressed on.
    """
    said = " ".join(text.split())
    if CAPTCHA_REJECTED.search(said):
        return "captcha_rejected"
    if is_confirmation(url) or CONFIRMED_WORDS.search(said):
        return "submitted"
    if challenge:
        return "challenge_shown"
    errors = field_errors if field_errors is not None else len(FIELD_ERROR_WORDS.findall(said))
    if errors > 0 and normalize_url(url) == normalize_url(before_url):
        return "validation"
    return "unconfirmed"


def normalize_url(url: str) -> str:
    """A URL without its fragment or trailing slash, for asking whether a press moved it."""
    return url.split("#", 1)[0].rstrip("/")


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


class Scorecard:
    """`attempts.jsonl`: one line per press, written before the next attempt starts."""

    def __init__(self, directory: Path) -> None:
        self.path = directory / "attempts.jsonl"

    def write(
        self,
        *,
        listing_id: str,
        url: str,
        experiment: int,
        knobs: dict[str, Any],
        debugger_attached_at_press: bool,
        input_kind: str,
        fill_seconds: float,
        outcome: str,
        screenshot: str | None,
    ) -> dict[str, Any]:
        """Append the attempt; a scorecard that cannot be written raises, never passes quietly."""
        if outcome not in OUTCOMES:
            raise ValueError(f"Unknown outcome {outcome!r}")
        if input_kind not in {"cdp", "os"}:
            raise ValueError(f"Unknown input kind {input_kind!r}")
        line = {
            "ts": now_iso(), "listing_id": listing_id, "url": url, "ats": url_host(url),
            "experiment": experiment, "knobs": knobs,
            "debugger_attached_at_press": debugger_attached_at_press,
            "input_kind": input_kind, "fill_seconds": round(fill_seconds, 1),
            "outcome": outcome, "email_confirmed": None, "screenshot": screenshot,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as handle:
            handle.write(json.dumps(line, default=str) + "\n")
        return line

    def recent(self, count: int = 20) -> list[dict[str, Any]]:
        """The last `count` attempts, oldest first; an unreadable line is skipped."""
        try:
            lines = self.path.read_text().splitlines()[-count:]
        except OSError:
            return []
        attempts: list[dict[str, Any]] = []
        for raw in lines:
            with suppress(ValueError):
                value = json.loads(raw)
                if isinstance(value, dict):
                    attempts.append(value)
        return attempts


class DriveFiles:
    """`files/`: the only files a driver's upload may hand Chrome, written by the agent."""

    def __init__(self, directory: Path) -> None:
        self.root = directory / "files"

    def write(self, name: str, data_b64: str) -> Path:
        """Store an upload; the message says why one is refused."""
        if not name or name != Path(name).name or "\\" in name or name.startswith("."):
            raise ValueError("an upload is a plain file name")
        if Path(name).suffix.lower() not in UPLOAD_SUFFIXES:
            raise ValueError("uploads are .pdf or .docx only")
        if len(data_b64) > (UPLOAD_BYTES + 2) // 3 * 4 + 4:
            raise ValueError("uploads are 10 MB at most")
        try:
            data = base64.b64decode(data_b64, validate=True)
        except ValueError:
            raise ValueError("data_b64 is not base64") from None
        if not data or len(data) > UPLOAD_BYTES:
            raise ValueError("uploads are 10 MB at most and not empty")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        handle, temporary = tempfile.mkstemp(dir=self.root, prefix=f".{name}.")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
            os.replace(temporary, path)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary)
            raise
        return path
