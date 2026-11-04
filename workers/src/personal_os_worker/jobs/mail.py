"""Account mail: the verification and reset messages an ATS sends, read-only.

An account wall that creates or resets an account is finished from a mailed
link or code. The inbox is opened read-only (EXAMINE) and every body is fetched
with BODY.PEEK, so nothing here marks, moves or deletes mail. Only a message
that arrived after the worker's own click and names this tenant or company is
used: an earlier tenant's verification mail is still sitting in the inbox.
"""

from __future__ import annotations

import contextlib
import email
import imaplib
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Literal, cast

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
MailKind = Literal["verify", "reset"]

# The ATS families a host can belong to, and the words their mail is sent under.
FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("myworkday", ("myworkday", "workday")),
    ("workday", ("workday",)),
    ("icims", ("icims",)),
    ("taleo", ("taleo",)),
    ("successfactors", ("successfactors",)),
    ("oraclecloud", ("oracle",)),
    ("smartrecruiters", ("smartrecruiters",)),
    ("jobvite", ("jobvite",)),
    ("avature", ("avature",)),
)
FAMILY_WORDS = frozenset(word for _, words in FAMILIES for word in words)
# Words that say nothing about which employer a host or a name belongs to.
GENERIC_WORDS = frozenset(
    {
        "www", "com", "careers", "career", "jobs", "job", "apply", "recruiting", "hire",
        "talent", "external", "inc", "llc", "ltd", "corp", "corporation", "company",
        "group", "holdings", "the", "and", "technologies", "technology", "systems",
        "solutions", "labs", "international", "global", "usa",
    }
)
SHARD = re.compile(r"^(?:wd\d+|www\d*|\d+)$")

KIND_WORDS: dict[MailKind, re.Pattern[str]] = {
    "verify": re.compile(r"verif|confirm|activat|validat", re.IGNORECASE),
    "reset": re.compile(r"reset|password", re.IGNORECASE),
}
NEVER_A_LINK = re.compile(
    r"unsubscribe|privacy|preferences|opt[- ]?out|/legal\b|terms of", re.IGNORECASE
)
CODE = re.compile(r"\bcode\b(.{0,80})", re.IGNORECASE | re.DOTALL)
DIGITS = re.compile(r"(?<!\d)\d{4,8}(?!\d)")
# Greenhouse mails a code of letters and digits; a word of letters alone never counts.
MIXED = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z]*\d)(?=\d*[A-Za-z])[A-Za-z0-9]{6,10}(?![A-Za-z0-9])"
)
URL = re.compile(r"https://[^\s<>\"')\]]+")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# A sender's clock may run a little behind the click that asked it to write.
CLOCK_SLACK = timedelta(seconds=60)
# How many of the newest messages one poll reads.
RECENT = 25


class MailUnavailable(RuntimeError):
    """The inbox could not be read. The message never holds the app password."""


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^a-z0-9]+", text.casefold())
        if len(token) >= 3 and token not in GENERIC_WORDS
    ]


def sender_hints(host: str, company: str) -> tuple[str, ...]:
    """The words this tenant's mail is sent under: tenant, ATS family, company."""
    specific: list[str] = []
    family: list[str] = []
    for label in host.casefold().split(".")[:-1]:
        if SHARD.match(label):
            continue
        words = next((words for key, words in FAMILIES if key in label), None)
        if words is not None:
            family.extend(words)
            continue
        specific.extend(_tokens(label))
    return tuple(dict.fromkeys([*specific, *family, *_tokens(company)]))


def matches(message: EmailMessage, hints: tuple[str, ...]) -> bool:
    """Whether the sender or subject names this tenant or company.

    The ATS family alone is not enough: every Workday tenant mails from
    myworkday.com, so it counts only when no tenant or company word is known.
    """
    haystack = f"{message.get('From', '')} {message.get('Subject', '')}".casefold()
    specific = [hint for hint in hints if hint not in FAMILY_WORDS]
    return any(hint in haystack for hint in specific or hints)


class _Html(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href") or None
            self._anchor = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((" ".join("".join(self._anchor).split()), self._href))
            self._href = None

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._href is not None:
            self._anchor.append(data)


def _part(message: EmailMessage, kind: str) -> str:
    body = message.get_body(preferencelist=(kind,))
    if body is None:
        return ""
    try:
        return str(cast(EmailMessage, body).get_content())
    except (LookupError, ValueError):
        return ""


def _links(message: EmailMessage) -> list[tuple[str, str]]:
    """Every link with the words around it: the HTML anchors, then the text's URLs."""
    parser = _Html()
    parser.feed(_part(message, "html"))
    found = list(parser.links)
    for line in _part(message, "plain").splitlines():
        found.extend((line, url) for url in URL.findall(line))
    return found


def action_link(message: EmailMessage, kind: MailKind) -> str | None:
    """The mailed link that verifies the account, or resets its password."""
    for text, url in _links(message):
        if not url.startswith("https://"):
            continue
        label = f"{text} {url}"
        if NEVER_A_LINK.search(label):
            continue
        if KIND_WORDS[kind].search(label):
            return url
    return None


def _text(message: EmailMessage) -> str:
    plain = _part(message, "plain")
    if plain:
        return plain
    parser = _Html()
    parser.feed(_part(message, "html"))
    return " ".join(parser.text)


def verification_code(message: EmailMessage) -> str | None:
    """A 4-8 digit code, or 6-10 mixed letters and digits, after the word "code" in the body."""
    text = _text(message)
    for pattern in (DIGITS, MIXED):
        for found in CODE.finditer(text):
            code = pattern.search(found.group(1))
            if code:
                return code.group(0)
    return None


def parse_message(raw: bytes) -> EmailMessage:
    return email.message_from_bytes(raw, policy=policy.default)


def _sent_at(message: EmailMessage) -> datetime | None:
    try:
        sent = parsedate_to_datetime(str(message.get("Date", "")))
    except (TypeError, ValueError):
        return None
    return sent if sent.tzinfo else sent.replace(tzinfo=UTC)


class Inbox:
    """The account mailbox over IMAP, opened read-only for each wait."""

    def __init__(
        self,
        user: str,
        password: str,
        *,
        connect: Callable[[], imaplib.IMAP4] | None = None,
        poll_seconds: float = 4.0,
    ) -> None:
        self.user = user
        self._password = password
        self._connect = connect or self._gmail
        self.poll_seconds = poll_seconds

    def _gmail(self) -> imaplib.IMAP4:
        client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
        client.login(self.user, self._password)
        return client

    def await_message(
        self, *, since: datetime, hints: tuple[str, ...], timeout: float = 180.0
    ) -> EmailMessage | None:
        """The newest message from this tenant since the click, or None at the timeout."""
        deadline = time.monotonic() + timeout
        try:
            client = self._connect()
        except (imaplib.IMAP4.error, OSError) as error:
            raise MailUnavailable(f"cannot open the inbox: {error}"[:300]) from error
        try:
            while True:
                found = self._newest(client, since, hints)
                if found is not None:
                    return found
                if time.monotonic() >= deadline:
                    return None
                time.sleep(self.poll_seconds)
        except (imaplib.IMAP4.error, OSError) as error:
            raise MailUnavailable(f"reading the inbox failed: {error}"[:300]) from error
        finally:
            with contextlib.suppress(Exception):
                client.logout()

    def _newest(
        self, client: imaplib.IMAP4, since: datetime, hints: tuple[str, ...]
    ) -> EmailMessage | None:
        # EXAMINE each poll: it is read-only, and it sees mail that arrived since.
        client.select("INBOX", readonly=True)
        # SEARCH SINCE is by day in the server's zone; a day early misses nothing.
        day = (since - timedelta(days=1)).astimezone(UTC)
        imap_day = f"{day.day:02d}-{MONTHS[day.month - 1]}-{day.year}"
        status, data = client.search(None, "SINCE", imap_day)
        numbers = (data[0] or b"").split() if status == "OK" and data else []
        for number in reversed(numbers[-RECENT:]):
            status, parts = client.fetch(number.decode(), "(BODY.PEEK[])")
            raw = next((part[1] for part in parts or [] if isinstance(part, tuple)), None)
            if status != "OK" or not isinstance(raw, bytes):
                continue
            message = parse_message(raw)
            sent = _sent_at(message)
            if sent is None or sent < since - CLOCK_SLACK:
                continue
            if matches(message, hints):
                return message
        return None
