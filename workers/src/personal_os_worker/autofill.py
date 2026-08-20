from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


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

BLOCKED_LABEL_WORDS = frozenset(
    {
        "agree",
        "agreement",
        "acknowledge",
        "certification",
        "gender",
        "hispanic",
        "latino",
        "military",
        "orientation",
        "pronoun",
        "pronouns",
        "race",
        "racial",
        "ethnicity",
        "ethnic",
        "veteran",
        "disability",
        "disabled",
        "demographic",
        "demographics",
        "eeo",
        "equal employment",
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


def is_sensitive_field(field: dict[str, Any]) -> bool:
    context = normalize_question(
        " ".join(str(field.get(key) or "") for key in ("label", "name", "group"))
    )
    return any(
        re.search(rf"(?<!\w){re.escape(word)}(?!\w)", context)
        for word in BLOCKED_LABEL_WORDS
    )


def build_autofill_plan(
    fields: list[dict[str, Any]],
    *,
    profile: dict[str, Any],
    approved_answers: list[dict[str, Any]],
) -> AutofillPlan:
    answer_candidates: dict[str, list[str]] = {}
    for item in approved_answers:
        question = normalize_question(str(item.get("normalized_question", "")))
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
    resume_candidates = {
        int(field["index"])
        for field in fields
        if str(field.get("type") or "").casefold() == "file"
        and not is_sensitive_field(field)
        and any(
            word in normalize_question(str(field.get("label") or ""))
            for word in ("resume", "cv")
        )
    }

    for field in fields:
        label = str(field.get("label") or "").strip()
        normalized = normalize_question(label)
        field_type = str(field.get("type") or "").casefold()
        tag = str(field.get("tag") or "").casefold()
        sensitive = is_sensitive_field(field)
        required = field.get("required") is True
        index = int(field["index"])

        if field_type in {"submit", "button", "reset", "hidden"} or tag == "button":
            continue

        value: str | None = None
        kind = "text"
        if sensitive:
            pass
        elif field_type == "file" and index in resume_candidates and len(resume_candidates) == 1:
            kind = "resume"
        elif field_type not in {"checkbox", "radio", "file"}:
            profile_key = PROFILE_LABELS.get(normalized)
            profile_value = resolve_profile_value(profile, profile_key)
            if isinstance(profile_value, bool):
                value = "Yes" if profile_value else "No"
            elif profile_value is not None and str(profile_value).strip():
                value = str(profile_value).strip()
            elif profile_key is None and normalized in answers:
                value = answers[normalized]

        if kind == "resume" or value is not None:
            fills.append(FormFill(index=index, kind=kind, value=value))
        elif required and label:
            unanswered.append(label)

    return AutofillPlan(tuple(fills), tuple(dict.fromkeys(unanswered)))


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
    return profile.get(profile_key) if profile_key else None
