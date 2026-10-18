"""Account mail: the right message, its link or code, and a read-only inbox."""

from __future__ import annotations

import imaplib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.jobs.mail import (
    Inbox,
    MailUnavailable,
    action_link,
    matches,
    parse_message,
    sender_hints,
    verification_code,
)

MAIL = Path(__file__).parent / "fixtures" / "mail"
VERIFY_URL = (
    "https://acme.wd5.myworkdayjobs.com/en-US/External/activate/7f3c9a2e1b"
    "?redirect=%2Fen-US%2FExternal"
)
RESET_URL = (
    "https://acme.wd5.myworkdayjobs.com/en-US/External/passwordreset/4d2b8e"
    "?redirect=%2Fen-US%2FExternal"
)
SINCE = datetime(2026, 10, 18, 21, 5, tzinfo=UTC)


def raw(name: str) -> bytes:
    return (MAIL / name).read_bytes()


def load(name: str):  # type: ignore[no-untyped-def]
    return parse_message(raw(name))


class FakeImap:
    """An IMAP server holding messages in arrival order, recording every command."""

    def __init__(self, messages: list[bytes]) -> None:
        self.messages = messages
        self.commands: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> tuple[str, list[Any]]:
            self.commands.append((name, args, kwargs))
            if name == "search":
                ids = " ".join(str(index + 1) for index in range(len(self.messages)))
                return "OK", [ids.encode()]
            if name == "fetch":
                number = int(args[0])
                body = self.messages[number - 1]
                return "OK", [(f"{number} (BODY[] {{{len(body)}}}".encode(), body), b")"]
            return "OK", [b""]

        return record


def test_workday_verify_link() -> None:
    assert action_link(load("workday_verify.eml"), "verify") == VERIFY_URL


def test_reset_link() -> None:
    assert action_link(load("workday_reset.eml"), "reset") == RESET_URL


def test_code() -> None:
    assert verification_code(load("workday_code.eml")) == "482913"


def test_no_code_in_a_link_mail() -> None:
    assert verification_code(load("other_tenant_verify.eml")) is None


def test_sender_hints_name_the_tenant_and_the_company() -> None:
    hints = sender_hints("acme.wd5.myworkdayjobs.com", "Acme Corporation")

    assert hints[:3] == ("acme", "myworkday", "workday")
    assert "corporation" not in hints


def test_matches_only_this_tenant() -> None:
    hints = sender_hints("acme.wd5.myworkdayjobs.com", "Acme Corporation")

    assert matches(load("workday_code.eml"), hints)
    assert not matches(load("other_tenant_verify.eml"), hints)


def test_await_ignores_mail_before_since_and_other_tenants() -> None:
    server = FakeImap(
        [raw("workday_verify.eml"), raw("workday_code.eml"), raw("other_tenant_verify.eml")]
    )
    inbox = Inbox("e@x.com", "app-pw", connect=lambda: server, poll_seconds=0)

    found = inbox.await_message(
        since=SINCE, hints=sender_hints("acme.wd5.myworkdayjobs.com", "Acme"), timeout=1
    )

    assert found is not None
    assert found["Message-ID"] == "<code.482913@myworkday.com>"


def test_await_times_out() -> None:
    inbox = Inbox("e@x.com", "app-pw", connect=lambda: FakeImap([]), poll_seconds=0)

    assert inbox.await_message(since=SINCE, hints=("acme",), timeout=0.01) is None


def test_never_sets_flags() -> None:
    server = FakeImap([raw("workday_verify.eml"), raw("workday_code.eml")])
    inbox = Inbox("e@x.com", "app-pw", connect=lambda: server, poll_seconds=0)

    inbox.await_message(since=SINCE, hints=("nobody-matches",), timeout=0.01)

    names = [name for name, _, _ in server.commands]
    assert "fetch" in names
    assert not {"store", "expunge", "copy", "move", "uid", "append"} & set(names)
    for name, args, kwargs in server.commands:
        if name == "fetch":
            assert "PEEK" in str(args[1])
        if name == "select":
            assert kwargs.get("readonly") is True


def test_login_failure_is_mail_unavailable() -> None:
    def refuse() -> imaplib.IMAP4:
        raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

    inbox = Inbox("e@x.com", "app-pw", connect=refuse, poll_seconds=0)
    with pytest.raises(MailUnavailable, match="AUTHENTICATIONFAILED") as raised:
        inbox.await_message(since=SINCE, hints=("acme",), timeout=0.01)
    assert "app-pw" not in str(raised.value)


def test_await_skips_stale_mail_from_this_tenant() -> None:
    """The last tenant's mail, still in the inbox, is never this wall's answer."""
    server = FakeImap([raw("workday_verify.eml")])
    inbox = Inbox("e@x.com", "app-pw", connect=lambda: server, poll_seconds=0)

    assert inbox.await_message(since=SINCE, hints=("acme",), timeout=0.01) is None
