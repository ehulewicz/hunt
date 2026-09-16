from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from personal_os_worker.adapters import is_non_answer_option, option_match

FILE_KINDS = frozenset({"resume", "transcript"})
PriorityPolicy = Callable[[dict[str, Any]], str | None]


@dataclass(frozen=True, slots=True)
class FormFill:
    index: int
    kind: str
    value: str | None
    # A choice made against the live menu rather than planned up front: "source"
    # (how did you hear) or "consent" (terms, privacy, attestations).
    chooser: str | None = None


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

# Consent, terms, and attestation controls are always answered affirmatively
# (decided 2026-09-14); only a signature is left to the user. Demographic
# questions are ordinary saved answers for a single user and are not blocked.
CONSENT_WORDS = frozenset(
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
    }
)
SIGNATURE_WORDS = frozenset({"signature", "electronic signature"})
BLOCKED_LABEL_WORDS = CONSENT_WORDS | SIGNATURE_WORDS
NEGATION = re.compile(r"\b(not|no|don t|do not|decline|disagree|reject|refuse)\b")
AFFIRMATION = re.compile(
    r"\b(yes|agree|accept|acknowledge|consent|confirm|i have read|understood)\b"
)
JOB_BOARD = re.compile(r"\b(job\s+boards?|linkedin|indeed|handshake|glassdoor|wellfound|dice)\b")

HOW_HEARD = re.compile(
    r"\bhow did you (?:first )?hear\b|\bwhere did you (?:first )?hear\b|"
    r"\bhow did you (?:first )?(?:find|learn about)\b",
    re.IGNORECASE,
)
AUTHORIZATION_EXPIRY = re.compile(
    r"\b(expir(?:e|es|ation)|valid (?:until|through)|end date)\b.*"
    r"\b(visa|permit|work authori[sz]ation)\b|"
    r"\b(visa|permit|work authori[sz]ation)\b.*"
    r"\b(expir(?:e|es|ation)|valid (?:until|through)|end date)\b",
    re.IGNORECASE | re.DOTALL,
)
FACTUAL_DATE = re.compile(
    r"\b\d{4}-\d{2}(?:-\d{2})?\b|"
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\b.*\b\d{4}\b",
    re.IGNORECASE,
)


def normalize_question(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_answer_question(value: str) -> str:
    """Normalize harmless punctuation differences for exact answer reuse."""
    return re.sub(r"[^a-z0-9+#]+", " ", value.casefold()).strip()


def profile_label_key(label: str) -> str | None:
    """Return a canonical profile key, accepting only safe leading-Your aliases."""
    normalized = normalize_question(label)
    key = PROFILE_LABELS.get(normalized)
    if key is not None:
        return key
    if normalized.startswith("your "):
        return PROFILE_LABELS.get(normalized.removeprefix("your "))
    return None


def _is_company_site(value: str) -> bool:
    """"Company Website", "DV Website", "Careers page": the employer's own site."""
    words = set(value.split())
    if not words & {"website", "site", "careers", "career", "webpage"}:
        return False
    if JOB_BOARD.search(value):
        return False
    return "job" not in words or "company" in words


def preferred_source_option(question: str, options: list[str]) -> str | None:
    """The user's standing source choice, ranked by the options actually offered."""
    if not HOW_HEARD.search(question):
        return None

    normalized = [(option, normalize_answer_question(option)) for option in options]
    preferences = (
        _is_company_site,
        lambda value: re.search(r"\bjob\s+boards?\b", value) is not None,
        lambda value: value == "other" or value.startswith("other "),
    )
    for matches in preferences:
        found = [option for option, value in normalized if matches(value)]
        if found:
            return found[0]
    return None


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


def _field_context(field: dict[str, Any]) -> str:
    return normalize_question(
        " ".join(str(field.get(key) or "") for key in ("label", "name", "group"))
    )


def _mentions(context: str, words: frozenset[str]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", context) for word in words)


def is_cover_letter_field(field: dict[str, Any]) -> bool:
    """A textarea or file control asking for the cover letter, by label, name, or id."""
    if str(field.get("tag") or "").casefold() != "textarea" and (
        str(field.get("type") or "").casefold() != "file"
    ):
        return False
    hints = normalize_question(
        " ".join(str(field.get(key) or "") for key in ("label", "name", "selector"))
    )
    return re.search(r"cover[\s_-]*letter", hints) is not None


def is_sensitive_field(field: dict[str, Any]) -> bool:
    """Consent, attestation, or signature: never drafted, never put to Claude."""
    return _mentions(_field_context(field), BLOCKED_LABEL_WORDS)


def is_consent_field(field: dict[str, Any]) -> bool:
    """A consent or attestation control the worker answers affirmatively; not a signature."""
    context = _field_context(field)
    return _mentions(context, CONSENT_WORDS) and not _mentions(context, SIGNATURE_WORDS)


def affirmative_option(options: list[str]) -> str | None:
    """The one option that agrees: "Yes", "I agree", "Yes, I have read and accept ..."."""
    candidates = [
        option
        for option in options
        if option.strip()
        and not is_non_answer_option(option)
        and not NEGATION.search(normalize_answer_question(option))
    ]
    agreeing = [
        option for option in candidates if AFFIRMATION.search(normalize_answer_question(option))
    ]
    if len(agreeing) == 1:
        return agreeing[0]
    return candidates[0] if len(candidates) == 1 and not agreeing else None


def build_autofill_plan(
    fields: list[dict[str, Any]],
    *,
    profile: dict[str, Any],
    approved_answers: list[dict[str, Any]],
    priority: PriorityPolicy | None = None,
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
        chooser: str | None = None
        kind = "text"
        if sensitive:
            if is_consent_field(field) and field_type != "file":
                own = str(field.get("label") or field.get("value") or "")
                if field_type in {"radio", "checkbox"}:
                    options = group_options.get(normalized) or [own]
                    chosen = own if len(options) == 1 else affirmative_option(options)
                    value = own if chosen == own else None
                elif field.get("role") == "combobox":
                    chooser = "consent"
                elif tag == "select":
                    options = [str(option) for option in field.get("options") or []]
                    value = affirmative_option(options)
        elif field_type == "file" and index in resume_candidates and len(resume_candidates) == 1:
            kind = "resume"
        elif (
            field_type == "file"
            and index in transcript_candidates
            and len(transcript_candidates) == 1
        ):
            kind = "transcript"
        elif field_type != "file":
            options = (
                group_options.get(normalized, [])
                if field_type in {"radio", "checkbox"}
                else [str(option) for option in field.get("options") or []]
            )
            value = preferred_source_option(question_label, options)
            if value is None and not options and HOW_HEARD.search(question_label):
                # A typeahead shows its choices only once opened; rank them then.
                chooser = "source" if field.get("role") == "combobox" else None
            profile_key = profile_label_key(question_label)
            if str(field.get("section") or "") == "education":
                profile_key = {
                    "end date month": "graduation_month",
                    "end date year": "graduation_year",
                }.get(normalized, profile_key)
            profile_value = resolve_profile_value(profile, profile_key)
            exact_value = answers.get(answer_question) if profile_key is None else None
            expiration_question = AUTHORIZATION_EXPIRY.search(question_label) is not None
            if value is None:
                if isinstance(profile_value, bool):
                    value = "Yes" if profile_value else "No"
                elif profile_value is not None and str(profile_value).strip():
                    value = str(profile_value).strip()
            if value is None and priority is not None and question_label:
                value = priority(
                    dict(field)
                    | {
                        "label": question_label,
                        "options": options,
                    }
                )
            if value is None:
                if exact_value is not None and (
                    not expiration_question or FACTUAL_DATE.search(exact_value)
                ):
                    value = exact_value
                    chooser = None
                elif (
                    profile_key is None
                    and not expiration_question
                    and fallback is not None
                    and question_label
                ):
                    value = fallback(question_label)

            # Withholding information is never an autofill default. If an old
            # answer or a model suggests it, leave the control for the user.
            if value is not None and is_non_answer_option(value):
                value = None

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

        if kind in FILE_KINDS or value is not None or chooser is not None:
            fills.append(FormFill(index=index, kind=kind, value=value, chooser=chooser))
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
    seasonal = normalize_answer_question(answer).replace("autumn", "fall")
    seasonal_hits = [
        option
        for option in options
        if normalize_answer_question(option).replace("autumn", "fall") == seasonal
    ]
    if len(seasonal_hits) == 1:
        return seasonal_hits[0]
    return choose_numeric_range(options, answer) or choose_synonym(options, answer)


# Forms word the same identity differently; the profile holds one word.
SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"male", "man", "cis male", "cis man", "cisgender male", "cisgender man"}),
    frozenset(
        {"female", "woman", "cis female", "cis woman", "cisgender female", "cisgender woman"}
    ),
    frozenset({"non binary", "nonbinary", "genderqueer", "gender non conforming"}),
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
