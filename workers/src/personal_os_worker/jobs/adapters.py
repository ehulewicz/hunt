from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

ADAPTER_VERSION = "personal-os-autofill-v1"


@dataclass(frozen=True, slots=True)
class AtsAdapter:
    name: str
    supported: bool = True
    resume_first: bool = False
    skip_choices_for_captcha: bool = False


ADAPTERS = {
    "greenhouse": AtsAdapter("greenhouse", resume_first=True),
    "ashby": AtsAdapter("ashby", resume_first=True),
    "lever": AtsAdapter("lever", skip_choices_for_captcha=True),
    "workday": AtsAdapter("workday", supported=False),
    "generic": AtsAdapter("generic", supported=False),
}
ATS_NAMES = tuple(ADAPTERS)


def adapter_version() -> str:
    commit = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "diff", "--quiet", "--", "workers/src/personal_os_worker"],
        check=False,
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


def detect_ats(url: str) -> AtsAdapter:
    host = (urlparse(url).hostname or "").casefold()
    if "greenhouse.io" in host or "greenhouse.com" in host:
        return ADAPTERS["greenhouse"]
    if "ashbyhq.com" in host:
        return ADAPTERS["ashby"]
    if "lever.co" in host:
        return ADAPTERS["lever"]
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        return ADAPTERS["workday"]
    return ADAPTERS["generic"]


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
