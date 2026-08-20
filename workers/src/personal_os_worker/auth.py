"""Supabase PKCE loopback sign-in for the Jobs CLI.

The CLI holds a real Google session rather than a second credential system, so
the backend accepts exactly the token it already accepts from a browser. The
loopback redirect plus PKCE is the standard native-app pattern from RFC 8252:
an intercepted authorization code is useless without the verifier, which never
leaves this process.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx

# Must match an entry in the Supabase redirect allowlist. A fixed port keeps the
# allowlist unambiguous; Supabase documents globbing for paths, not for ports.
CALLBACK_PORT = 54321
CALLBACK_PATH = "/callback"
# Refresh a little early so a request never races the expiry.
EXPIRY_MARGIN_SECONDS = 120

CHROME_EXE = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
CHROME_PROFILE = r"C:\Users\eahtr\personal-os-chrome"

_DONE_PAGE = (
    b"<!doctype html><meta charset=utf-8><title>Signed in</title>"
    b"<body style='font:16px system-ui;padding:3rem'>"
    b"<h1>Signed in</h1><p>You can close this tab and return to the terminal.</p>"
)
_FAIL_PAGE = (
    b"<!doctype html><meta charset=utf-8><title>Sign-in failed</title>"
    b"<body style='font:16px system-ui;padding:3rem'>"
    b"<h1>Sign-in failed</h1><p>Check the terminal for details.</p>"
)


class LoginError(RuntimeError):
    """Raised when interactive sign-in cannot be completed."""


@dataclass(frozen=True, slots=True)
class Session:
    access_token: str
    refresh_token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - EXPIRY_MARGIN_SECONDS


@dataclass(frozen=True, slots=True)
class SupabaseAuth:
    url: str
    publishable_key: str

    @property
    def authorize_url(self) -> str:
        return f"{self.url.rstrip('/')}/auth/v1/authorize"

    @property
    def token_url(self) -> str:
        return f"{self.url.rstrip('/')}/auth/v1/token"


def session_path() -> Path:
    configured = os.environ.get("PERSONAL_OS_SESSION_FILE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "personal-os" / "session.json"


def save_session(session: Session) -> Path:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expires_at": session.expires_at,
            },
            indent=2,
        )
        + "\n"
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
    return path


def load_session() -> Session | None:
    try:
        data = json.loads(session_path().read_text())
        return Session(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=float(data["expires_at"]),
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _session_from_payload(payload: dict[str, Any]) -> Session:
    try:
        access_token = str(payload["access_token"])
        refresh_token = str(payload["refresh_token"])
    except KeyError as error:
        raise LoginError("Supabase response did not include tokens") from error
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, int | float):
        expires_at = time.time() + float(payload.get("expires_in", 3600))
    return Session(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=float(expires_at),
    )


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # BaseHTTPRequestHandler requires this exact name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        error = query.get("error_description", query.get("error", [None]))[0]
        handler = type(self)
        handler.code = code
        handler.error = error
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_DONE_PAGE if code else _FAIL_PAGE)

    def log_message(self, *_: Any) -> None:
        """Silence the default stderr request logging."""


def open_in_browser(url: str) -> None:
    """Open the automation Chrome profile, which already holds the Google session."""
    if Path(CHROME_EXE).exists():
        subprocess.Popen(  # fixed executable path; the URL is constructed here
            [CHROME_EXE, f"--user-data-dir={CHROME_PROFILE}", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    raise LoginError(f"Windows Chrome not found at {CHROME_EXE}")


def login(auth: SupabaseAuth, *, timeout_seconds: float = 300.0) -> Session:
    verifier, challenge = _pkce_pair()
    redirect_to = f"http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}"
    query = urllib.parse.urlencode(
        {
            "provider": "google",
            "redirect_to": redirect_to,
            "code_challenge": challenge,
            "code_challenge_method": "s256",
        }
    )
    try:
        server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _CallbackHandler)
    except OSError as error:
        raise LoginError(
            f"Port {CALLBACK_PORT} is already in use, so the sign-in redirect cannot "
            "be received. Close whatever is holding it and try again."
        ) from error

    _CallbackHandler.code = None
    _CallbackHandler.error = None
    server.timeout = timeout_seconds
    try:
        open_in_browser(f"{auth.authorize_url}?{query}")
        print(f"Opened Chrome for Google sign-in. Waiting on {redirect_to} ...")
        server.handle_request()
    finally:
        server.server_close()

    if _CallbackHandler.error:
        raise LoginError(f"Supabase rejected the sign-in: {_CallbackHandler.error}")
    code = _CallbackHandler.code
    if not code:
        raise LoginError("No authorization code arrived before the timeout")

    response = httpx.post(
        auth.token_url,
        params={"grant_type": "pkce"},
        headers={"apikey": auth.publishable_key, "Content-Type": "application/json"},
        json={"auth_code": code, "code_verifier": verifier},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise LoginError(
            f"Token exchange failed with {response.status_code}: {response.text[:300]}"
        )
    return _session_from_payload(response.json())


def refresh(auth: SupabaseAuth, session: Session) -> Session:
    response = httpx.post(
        auth.token_url,
        params={"grant_type": "refresh_token"},
        headers={"apikey": auth.publishable_key, "Content-Type": "application/json"},
        json={"refresh_token": session.refresh_token},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise LoginError(f"Token refresh failed with {response.status_code}: {response.text[:300]}")
    return _session_from_payload(response.json())


def active_session(auth: SupabaseAuth) -> Session:
    """Return a usable session, refreshing and re-saving it when it has aged out."""
    session = load_session()
    if session is None:
        raise LoginError("Not signed in. Run 'hunt login' first.")
    if not session.expired:
        return session
    refreshed = refresh(auth, session)
    save_session(refreshed)
    return refreshed


def port_is_free(port: int = CALLBACK_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True
