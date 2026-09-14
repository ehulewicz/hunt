from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from personal_os_worker.adapters import option_match

FILE_KINDS = frozenset({"resume", "transcript"})


@dataclass(frozen=True, slots=True)
class FormFill:
    index: int
    kind: str
    value: str | None


@dataclass(frozen=True, slots=True)
class AutofillPlan:
    fills: tuple[FormFill, ...]
    unanswered_required_questions: tuple[str, ...]


PROFILE_LABELS = {
    "first name": "legal_first_name",
    "given name": "legal_first_name",
    "last name": "legal_last_name",
    "family name": "legal_last_name",
    "surname": "legal_last_name",
    "name": "legal_name",
    "full name": "legal_name",
    "legal name": "legal_name",
    "full legal name": "legal_name",
    "preferred name": "preferred_name",
    "email": "email",
    "email address": "email",
    "phone": "phone",
    "phone number": "phone",
    "address": "address_line_1",
    "street address": "address_line_1",
    "address line 1": "address_line_1",
    "address line 2": "address_line_2",
    "city": "city",
    "state": "region",
    "state/province": "region",
    "zip code": "postal_code",
    "postal code": "postal_code",
    "country": "country",
    "linkedin": "linkedin_url",
    "linkedin url": "linkedin_url",
    "github": "github_url",
    "github url": "github_url",
    "portfolio": "portfolio_url",
    "portfolio url": "portfolio_url",
    "school": "school_name",
    "school name": "school_name",
    "degree": "degree_type",
    "field of study": "field_of_study",
    "gpa": "gpa",
    "graduation month": "graduation_month",
    "graduation year": "graduation_year",
    "graduation date month": "graduation_month",
    "graduation date year": "graduation_year",
    "years of professional experience": "years_professional_experience",
    "how many years of experience do you have?": "years_professional_experience",
    "earliest start date": "earliest_start",
    "earliest available start date": "earliest_start",
    "what is your earliest available start date?": "earliest_start",
    "are you willing to relocate?": "willing_to_relocate",
    "are you willing to travel?": "willing_to_travel",
    "will you now or in the future require sponsorship?": "sponsorship_required",
    "do you require visa sponsorship?": "sponsorship_required",
    "are you authorized to work in the united states?": "work_authorization_status",
}

# The worker never attests, consents, or signs on the user's behalf. Demographic
# questions are ordinary saved answers for a single user and are no longer
# blocked from reuse.
BLOCKED_LABEL_WORDS = frozenset(
    {
        "agree",
        "agreement",
        "acknowledge",
        "certification",
        "consent",
        "certify",
        "attest",
        "terms",
        "privacy",
        "signature",
        "electronic signature",
    }
)


def normalize_question(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_answer_question(value: str) -> str:
    """Normalize harmless punctuation differences for exact answer reuse."""
    return re.sub(r"[^a-z0-9+#]+", " ", value.casefold()).strip()


def is_resume_field(field: dict[str, Any]) -> bool:
    """A file control for the resume, whether its label or only its id says so."""
    if str(field.get("type") or "").casefold() != "file":
        return False
    hints = normalize_question(
        " ".join(str(field.get(key) or "") for key in ("label", "name", "selector"))
    )
    return "resume" in hints or re.search(r"(?<![a-z])cv(?![a-z])", hints) is not None


def is_transcript_field(field: dict[str, Any]) -> bool:
    """A file control for a transcript, by label, name, or id."""
    if str(field.get("type") or "").casefold() != "file":
        return False
    hints = normalize_question(
        " ".join(str(field.get(key) or "") for key in ("label", "name", "selector"))
    )
    return "transcript" in hints


def is_sensitive_field(field: dict[str, Any]) -> bool:
    context = normalize_question(
        " ".join(str(field.get(key) or "") for key in ("label", "name", "group"))
    )
    return any(
        re.search(rf"(?<!\w){re.escape(word)}(?!\w)", context) for word in BLOCKED_LABEL_WORDS
    )


def build_autofill_plan(
    fields: list[dict[str, Any]],
    *,
    profile: dict[str, Any],
    approved_answers: list[dict[str, Any]],
    fallback: Callable[[str], str | None] | None = None,
) -> AutofillPlan:
    """Plan fills from profile facts, then exact answers, then `fallback(question)`.

    The fallback is how the pattern and Claude tiers plug in without the planner
    knowing about them. Choice groups pick the single option that equals, starts
    with, is a prefix of, or contains every word of the answer.
    """
    answer_candidates: dict[str, list[str]] = {}
    for item in approved_answers:
        if str(item.get("reuse_policy") or "exact") != "exact":
            continue
        question = normalize_answer_question(
            str(
                item.get("normalized_key")
                or item.get("normalized_question")
                or item.get("question")
                or ""
            )
        )
        answer = str(item.get("answer_text", "")).strip()
        if question and answer:
            answer_candidates.setdefault(question, []).append(answer)
    answers = {
        question: candidates[0]
        for question, candidates in answer_candidates.items()
        if len(candidates) == 1
    }
    fills: list[FormFill] = []
    unanswered: list[str] = []
    answered_groups: set[str] = set()
    pending_choices: list[tuple[str, str]] = []
    resume_candidates = {
        int(field["index"])
        for field in fields
        if is_resume_field(field) and not is_sensitive_field(field)
    }
    transcript_candidates = {
        int(field["index"])
        for field in fields
        if is_transcript_field(field) and not is_sensitive_field(field)
    }

    group_options: dict[str, list[str]] = {}
    # A choice group with any option checked is answered; a control with a
    # value is answered. Neither is planned again by any tier.
    filled_groups: set[str] = set()
    for field in fields:
        if str(field.get("type") or "").casefold() in {"radio", "checkbox"} and field.get("group"):
            group_key = normalize_question(str(field["group"]))
            group_options.setdefault(group_key, []).append(
                str(field.get("label") or field.get("value") or "")
            )
            if field.get("filled") is True:
                filled_groups.add(group_key)

    for field in fields:
        label = str(field.get("label") or "").strip()
        field_type = str(field.get("type") or "").casefold()
        group = str(field.get("group") or "").strip()
        question_label = group if field_type in {"radio", "checkbox"} and group else label
        normalized = normalize_question(question_label)
        answer_question = normalize_answer_question(question_label)
        tag = str(field.get("tag") or "").casefold()
        sensitive = is_sensitive_field(field)
        required = field.get("required") is True
        index = int(field["index"])

        if field_type in {"submit", "button", "reset", "hidden"} or tag == "button":
            continue
        if field.get("filled") is True or (
            field_type in {"radio", "checkbox"} and normalized in filled_groups
        ):
            continue

        value: str | None = None
        kind = "text"
        if sensitive:
            pass
        elif field_type == "file" and index in resume_candidates and len(resume_candidates) == 1:
            kind = "resume"
        elif (
            field_type == "file"
            and index in transcript_candidates
            and len(transcript_candidates) == 1
        ):
            kind = "transcript"
        elif field_type != "file":
            profile_key = PROFILE_LABELS.get(normalized)
            if str(field.get("section") or "") == "education":
                profile_key = {
                    "end date month": "graduation_month",
                    "end date year": "graduation_year",
                }.get(normalized, profile_key)
            profile_value = resolve_profile_value(profile, profile_key)
            if isinstance(profile_value, bool):
                value = "Yes" if profile_value else "No"
            elif profile_value is not None and str(profile_value).strip():
                value = str(profile_value).strip()
            elif profile_key is None and answer_question in answers:
                value = answers[answer_question]
            elif profile_key is None and fallback is not None and question_label:
                value = fallback(question_label)

            if field_type in {"radio", "checkbox"} and value is not None:
                own = str(field.get("label") or field.get("value") or "")
                options = group_options.get(normalized) or [own]
                if field_type == "checkbox" and len(options) > 1 and "," in value:
                    # A comma-joined answer to a checkbox group is a multi-select:
                    # check every option named in it, by name, never by prefix.
                    value = own if any(same_name(own, part) for part in value.split(",")) else None
                else:
                    chosen = choose_option(options, value)
                    if chosen is None or chosen != own:
                        value = None

        if kind in FILE_KINDS or value is not None:
            fills.append(FormFill(index=index, kind=kind, value=value))
            if field_type in {"radio", "checkbox"} and group:
                answered_groups.add(normalized)
        elif required and label:
            if field_type in {"radio", "checkbox"} and group:
                pending_choices.append((normalized, question_label))
            else:
                unanswered.append(label)

    # A choice group counts as answered once any of its options is planned.
    unanswered.extend(
        label for group_key, label in pending_choices if group_key not in answered_groups
    )
    return AutofillPlan(tuple(fills), tuple(dict.fromkeys(unanswered)))


def same_name(option: str, answer: str) -> bool:
    """Whether two technology names are the same ("Typescript"/"TypeScript", "React"/"React.js")."""

    def key(value: str) -> str:
        text = normalize_answer_question(value).replace(" ", "")
        return text.removesuffix("js") if len(text) > 3 else text

    return key(option) == key(answer)


def choose_option(options: list[str], answer: str) -> str | None:
    """The single option an answer denotes, or None when none or several fit."""
    matched = option_match(options, answer)
    if matched is not None:
        return matched
    target = normalize_question(answer)
    prefixed = [
        option
        for option in options
        if normalize_question(option).startswith(target)
        or target.startswith(normalize_question(option))
    ]
    if len(prefixed) == 1:
        return prefixed[0]
    return choose_numeric_range(options, answer) or choose_synonym(options, answer)


# Forms word the same identity differently; the profile holds one word.
SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"male", "man", "cis male", "cis man", "cisgender male", "cisgender man"}),
    frozenset(
        {"female", "woman", "cis female", "cis woman", "cisgender female", "cisgender woman"}
    ),
    frozenset({"non binary", "nonbinary", "genderqueer", "gender non conforming"}),
    frozenset(
        {
            "prefer not to say",
            "prefer not to answer",
            "decline to self identify",
            "i don t wish to answer",
            "prefer not to disclose",
        }
    ),
)
QUALIFIER_WORDS = frozenset(
    {"cis", "cisgender", "he", "him", "his", "she", "her", "hers", "they", "them"}
)


def choose_synonym(options: list[str], answer: str) -> str | None:
    """The single option that means the same as the answer ("Male" -> "Cisgender man")."""
    target = normalize_answer_question(answer)
    group = next((group for group in SYNONYM_GROUPS if target in group), None)
    if group is None:
        return None
    hits: list[str] = []
    for option in options:
        words = normalize_answer_question(option).split()
        for length in range(len(words), 0, -1):
            head, tail = " ".join(words[:length]), words[length:]
            if head in group and all(word in QUALIFIER_WORDS for word in tail):
                hits.append(option)
                break
    return hits[0] if len(hits) == 1 else None


NUMBER = r"(\d+(?:\.\d+)?)"
RANGE_PATTERNS: tuple[tuple[re.Pattern[str], Callable[[float, float, float], bool]], ...] = (
    (re.compile(rf"^{NUMBER}\s*(?:-|\u2013|to)\s*{NUMBER}\b"), lambda v, lo, hi: lo <= v <= hi),
    (
        re.compile(rf"^{NUMBER}\s*(?:\+|or (?:above|higher|more|greater)|and (?:above|up))"),
        lambda v, lo, _: v >= lo,
    ),
    (
        re.compile(rf"^(?:above|over|more than|greater than)\s*{NUMBER}"),
        lambda v, lo, _: v > lo,
    ),
    (
        re.compile(rf"^(?:below|under|less than)\s*{NUMBER}"),
        lambda v, hi, _: v < hi,
    ),
    (
        re.compile(rf"^{NUMBER}\s*(?:or (?:below|less|lower)|and (?:below|under))"),
        lambda v, hi, _: v <= hi,
    ),
)


def choose_numeric_range(options: list[str], answer: str) -> str | None:
    """The single range option a number falls in ("3.78" -> "3.6-3.8"), else None."""
    try:
        value = float(re.sub(r"[^\d.]", "", answer.strip()) or "x")
    except ValueError:
        return None
    hits: list[str] = []
    for option in options:
        text = normalize_question(option)
        for pattern, holds in RANGE_PATTERNS:
            found = pattern.match(text)
            if found is None:
                continue
            bounds = [float(group) for group in found.groups() if group is not None]
            low = bounds[0]
            high = bounds[1] if len(bounds) > 1 else bounds[0]
            if holds(value, low, high):
                hits.append(option)
            break
    return hits[0] if len(hits) == 1 else None


def resolve_profile_value(profile: dict[str, Any], profile_key: str | None) -> Any:
    if profile_key == "legal_first_name":
        parts = str(profile.get("legal_name") or "").split()
        return parts[0] if parts else None
    if profile_key == "legal_last_name":
        parts = str(profile.get("legal_name") or "").split()
        return parts[-1] if len(parts) > 1 else None
    if profile_key == "work_authorization_status":
        value = normalize_question(str(profile.get(profile_key) or ""))
        if value in {
            "yes",
            "authorized",
            "authorized to work in the united states",
            "citizen",
            "us citizen",
            "u.s. citizen",
            "permanent resident",
            "green card holder",
        }:
            return "Yes"
        if value in {"no", "not authorized", "unauthorized"}:
            return "No"
        return None
    if profile_key == "earliest_start":
        return profile.get("earliest_start_text") or profile.get("earliest_start_date")
    if profile_key == "country":
        country = str(profile.get("country") or "").strip()
        return "United States" if country.casefold().startswith("united stat") else country
    if profile_key in {"graduation_month", "graduation_year"}:
        graduation = str(profile.get("graduation_date") or "").strip()
        matched = re.match(r"^(\d{4})-(\d{2})(?:-\d{2})?$", graduation)
        if matched is None:
            return None
        if profile_key == "graduation_year":
            return matched.group(1)
        month = int(matched.group(2))
        months = (
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        )
        return months[month] if 1 <= month <= 12 else None
    return profile.get(profile_key) if profile_key else None
