"""The two standing rules as the patterns and facts the hosted database holds (R68).

Consent is agreed to, and "how did you hear" names your standing source: each
is an active expression reading a fact, rendered by the formatter for that
fact. Tests that meet consent or a source question resolve through these.
"""

from __future__ import annotations

from typing import Any

from personal_os_worker.jobs.autofill import AnswerResolution
from personal_os_worker.jobs.resolve import Knowledge, Resolver

CONSENT_EXPRESSION = (
    r"^(?!.*\bsignature\b).*\b(agree|agreement|acknowledge|acknowledgment|acknowledgement"
    r"|certification|consent|certify|attest|terms|privacy)\b"
)
SOURCE_EXPRESSION = (
    r"\bhow did you (?:first )?hear\b|\bwhere did you (?:first )?hear\b"
    r"|\bhow did you (?:first )?(?:find|learn about)\b"
)
PATTERNS: list[dict[str, Any]] = [
    {"id": "standing-consent", "kind": "expression", "status": "active", "origin": "inbox",
     "expression": CONSENT_EXPRESSION, "fact_key": "consent.agrees",
     "resolver_id": "expression:standing-consent"},
    {"id": "standing-source", "kind": "expression", "status": "active", "origin": "inbox",
     "expression": SOURCE_EXPRESSION, "fact_key": "preferences.application_source",
     "resolver_id": "expression:standing-source"},
]
FACTS: dict[str, Any] = {
    "consent.agrees": True, "preferences.application_source": "Company website",
}


def standing(
    facts: dict[str, Any] | None = None, *, referral: dict[str, Any] | None = None
) -> Resolver:
    """A resolver that knows only the standing rules, and any facts given."""
    rows = [{"key": key, "value": value} for key, value in (FACTS | (facts or {})).items()]
    return Resolver(Knowledge.build(facts=rows, career=None, patterns=PATTERNS, referral=referral))


class Then:
    """Answers from `first` when it can, else from the standing rules."""

    def __init__(self, first: Any, rules: Resolver | None = None) -> None:
        self.first = first
        self.rules = rules or standing()
        self.knowledge = self.rules.knowledge

    def resolve(self, field: dict[str, Any]) -> AnswerResolution | None:
        return self.first.resolve(field) or self.rules.resolve(field)
