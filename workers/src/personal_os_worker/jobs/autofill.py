from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from personal_os_worker.jobs.adapters import is_non_answer_option
from personal_os_worker.jobs.options import (
    choose_option,
    normalize_answer_question,
    normalize_question,
    same_name,
)

FILE_KINDS = frozenset({"resume", "transcript"})
# Several chosen options in one value, as the formatters join them.
MULTI = "\n"


@dataclass(frozen=True, slots=True)
class AnswerResolution:
    value: str
    source: str
    resolver_id: str
    # The fact the value came from, the formatter that rendered it, and the
    # active pattern that chose the fact, when there was one.
    fact_key: str | None = None
    formatter: str | None = None
    pattern_id: str | None = None


class Resolves(Protocol):
    """Anything that answers a field: active patterns and the archive, or the model's verdicts."""

    def resolve(self, field: dict[str, Any]) -> AnswerResolution | None: ...


@dataclass(frozen=True, slots=True)
class FormFill:
    index: int
    kind: str
    value: str | None
    # A choice made against the live menu rather than planned up front: "source"
    # (how did you hear) or "consent" (terms, privacy, attestations).
    chooser: str | None = None
    resolution: AnswerResolution | None = None
    # Clear the control instead of setting it: uncheck a box a prior pass checked.
    clear: bool = False


@dataclass(frozen=True, slots=True)
class AutofillPlan:
    fills: tuple[FormFill, ...]
    unanswered_required_questions: tuple[str, ...]


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


# Greenhouse renders an essay prompt as the same single-line text input it uses
# for "First Name", capped at 255 characters; only the question text tells them
# apart. The control tag alone cannot decide what wants prose.
NARRATIVE_QUESTION = re.compile(
    r"\b(?:sentences|paragraphs?)\b"
    r"|\bin your own words\b"
    r"|\b(?:tell us|tell me|walk us through|elaborate on)\b"
    r"|\bwhy (?:do|are|would|did) you\b"
    r"|\b(?:describe|explain|share)\b[^.?!]*"
    r"\b(?:why|interest|motivation|experience|background|yourself|approach)\b",
    re.IGNORECASE,
)
PROSE_INPUT_TYPES = frozenset({"", "text", "search"})


def is_long_form_field(field: dict[str, Any]) -> bool:
    """A control that wants prose: every textarea, and a text input whose question asks for it."""
    if is_sensitive_field(field) or field.get("role") == "combobox":
        return False
    tag = str(field.get("tag") or "").casefold()
    if tag == "textarea":
        return True
    field_type = str(field.get("type") or "").casefold()
    if tag != "input" or field_type not in PROSE_INPUT_TYPES or field.get("options"):
        return False
    return NARRATIVE_QUESTION.search(_field_context(field)) is not None


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
    resolver: Resolves | None = None,
) -> AutofillPlan:
    """Plan fills: the engine's own rules, then whatever the resolver answers from facts.

    The engine handles consent, the resume and transcript uploads, and "how did
    you hear about us"; every other value comes from the resolver. Choice
    groups pick the single option that equals, starts with, is a prefix of, or
    contains every word of the answer.
    """
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
        resolution: AnswerResolution | None = None
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
            source_choice = preferred_source_option(question_label, options)
            if source_choice is not None:
                resolution = AnswerResolution(
                    source_choice, "pattern", "engine:application_source"
                )
            elif resolver is not None:
                resolution = resolver.resolve(
                    dict(field) | {"label": question_label, "options": options}
                )
            # A value that declines to answer is never chosen, whoever proposed it.
            if resolution is not None and is_non_answer_option(resolution.value):
                resolution = None
            value = resolution.value if resolution is not None else None
            if value is None and not options and HOW_HEARD.search(question_label):
                chooser = "source" if field.get("role") == "combobox" else None

            if field_type in {"radio", "checkbox"} and value is not None:
                own = str(field.get("label") or field.get("value") or "")
                options = group_options.get(normalized) or [own]
                if field_type == "checkbox" and len(options) > 1 and (
                    MULTI in value or "," in value
                ):
                    # Several answers to a checkbox group: check every option named,
                    # by name, never by prefix. A newline separates chosen options
                    # (which may hold commas); a comma separates typed names.
                    parts = value.split(MULTI) if MULTI in value else value.split(",")
                    value = own if any(
                        part.strip() == own.strip() or same_name(own, part) for part in parts
                    ) else None
                else:
                    chosen = choose_option(options, value)
                    if chosen is None or chosen != own:
                        value = None

        if kind in FILE_KINDS or value is not None or chooser is not None:
            fills.append(FormFill(
                index=index, kind=kind, value=value, chooser=chooser, resolution=resolution
            ))
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
