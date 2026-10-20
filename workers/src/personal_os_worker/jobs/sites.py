"""Site-independent helpers: option matching, non-answers, failure signatures, the ATS label."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

ADAPTER_VERSION = "personal-os-autofill-v1"


# Which applicant tracking system a URL is on. This is a dimension for querying
# fill events and filtering listings, never behavior: every site, named or not,
# is filled the same way, from its controls.
ATS_NAMES = ("greenhouse", "ashby", "lever", "workday", "generic")


def adapter_version() -> str:
    # Asked of this source's own checkout: a run may start anywhere, and the device
    # agent starts its runs in the home directory, where git has no repository.
    repo = Path(__file__).resolve().parents[4]
    commit = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "diff", "--quiet", "--", "workers/src/personal_os_worker"],
        check=False,
        capture_output=True,
        cwd=repo,
    ).returncode
    return f"{ADAPTER_VERSION}@{commit or 'unknown'}{'-dirty' if dirty else ''}"


def is_non_answer_option(value: str) -> bool:
    """Whether a choice declines to provide the requested information."""
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    return any(
        phrase in normalized
        for phrase in (
            "prefer not",
            "decline to",
            "do not wish",
            "don t wish",
            "do not want",
            "don t want",
            "choose not to",
            "rather not",
        )
    ) or normalized in {"decline", "no answer"}


def detect_ats(url: str) -> str:
    """The ATS a URL is on, or "generic"; a label, never a reason to fill differently."""
    host = (urlparse(url).hostname or "").casefold()
    if "greenhouse.io" in host or "greenhouse.com" in host:
        return "greenhouse"
    if "ashbyhq.com" in host:
        return "ashby"
    if "lever.co" in host:
        return "lever"
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        return "workday"
    return "generic"


def option_match(options: list[str], requested: str) -> str | None:
    if is_non_answer_option(requested):
        return None

    def normalized(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    target = normalized(requested)
    eligible = [option for option in options if not is_non_answer_option(option)]
    exact = [option for option in eligible if normalized(option) == target]
    if len(exact) == 1:
        return exact[0]
    tokens = set(target.split())
    candidates = [
        option
        for option in eligible
        if tokens and tokens <= set(normalized(option).split())
    ]
    return candidates[0] if len(candidates) == 1 else None


def failure_signature(ats: str, category: str, fingerprint: str) -> str:
    value = f"{ats}|{category}|{fingerprint}".encode()
    return hashlib.sha256(value).hexdigest()
