"""What a captured field asks for, and what its choice group and the question above hold."""

from __future__ import annotations

import re
from typing import Any

from personal_os_worker.jobs.options import normalize_question
from personal_os_worker.jobs.registry.formats.choices import polarity

# Consent, terms, and attestation controls are recognised here so they are never
# drafted or put to a model; the pattern naming `consent.agrees` answers them
# (R68), and a typed signature is signed with your legal name. Demographic
# questions are ordinary saved answers for a single user and are not blocked.
CONSENT_WORDS = frozenset(
    {
        "agree",
        "agreement",
        "acknowledge",
        "acknowledgment",
        "acknowledgement",
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
# A follow-up that applies only after one answer: "If you answered No to the
# above, ...", "If yes, please ...", "If so, ...". "If not" counts only as a whole
# clause, so "If not already provided" is no condition.
CONDITIONAL = re.compile(
    r"^\s*if\s+(?:(?:you\s+(?:answered|selected|chose|said)\s+)?[\"'\u201c\u2018]?(yes|no)\b"
    r"|(so|not)\s*[,.:;\u2013\u2014-])",
    re.IGNORECASE,
)
# An upload that fills the application in from a resume ("Easy Apply",
# "apply with resume") is a way into the form, not the form's resume field.
AUTOFILL_UPLOAD = re.compile(
    r"apply[\s_-]+with[\s_-]+(your[\s_-]+)?resume|easy[\s_-]*apply|auto[\s_-]*fill|\bparse"
)


def is_resume_field(field: dict[str, Any]) -> bool:
    """A file control for the resume, whether its label, its id, or what it sits in says so."""
    if str(field.get("type") or "").casefold() != "file":
        return False
    hints = normalize_question(
        " ".join(str(field.get(key) or "") for key in ("label", "name", "selector", "container"))
    )
    if AUTOFILL_UPLOAD.search(hints):
        return False
    return "resume" in hints or re.search(r"(?<![a-z])cv(?![a-z])", hints) is not None


def is_transcript_field(field: dict[str, Any]) -> bool:
    """A file control for a transcript, by label, name, or id."""
    if str(field.get("type") or "").casefold() != "file":
        return False
    hints = normalize_question(
        " ".join(str(field.get(key) or "") for key in ("label", "name", "selector", "container"))
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


def is_signature_field(field: dict[str, Any]) -> bool:
    """A signature you type: a box that asks for a signature, signed with your legal name."""
    return _mentions(_field_context(field), SIGNATURE_WORDS) and is_follow_up_box(field)


# Greenhouse renders an essay prompt as the same single-line text input it uses
# for "First Name", capped at 255 characters; only the question text tells them
# apart. The control tag alone cannot decide what wants prose.
NARRATIVE_QUESTION = re.compile(
    r"\b(?:sentences|paragraphs?)\b"
    r"|\bin your own words\b"
    r"|\b(?:tell us|tell me|walk us through|elaborate on)\b"
    r"|\bwhy (?:do|are|would|did) you\b"
    r"|\b(?:describe|explain|share)\b[^.?!]*"
    r"\b(?:why|interest|motivation|experience|background|yourself|approach)\b"
    r"|\bwhat (?:excites|interests|motivates|inspires|draws|attracts) you\b"
    r"|\bwhat do you (?:hope|want|wish|expect|plan) to"
    r" (?:learn|gain|achieve|accomplish|build|get)\b"
    r"|\bwhat do you think\b"
    r"|\b(?:favou?rite|proudest|most proud|proud of)\b"
    r"|^\s*why\s+[^?]{1,60}\?\s*$"
    r"|(?:^|[.?!]\s+)(?:please\s+)?(?:describe|explain)\s+"
    r"(?:a|an|the|your|one|how|why|what|in)\b"
    r"|\bshare (?:something|one (?:thing|project|example)"
    r"|an? (?:time|story|example|project|moment|experience))\b",
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


# A word that names a piece of information, not a story.
FACT_NOUNS = frozenset(
    {
        "name", "names", "link", "links", "url", "website", "profile", "portfolio",
        "github", "linkedin", "email", "e-mail", "phone", "address", "city", "state",
        "country", "zip", "postal", "school", "university", "college", "degree",
        "major", "minor", "gpa", "graduation", "pronoun", "pronouns", "certification",
        "certifications", "license", "licenses", "licence", "licences", "salary",
        "compensation", "employer", "company", "title", "date", "dates",
    }
)
# A word that asks for prose. "Other", "else" and "additional" only say
# "another one" ("Other Links" is still a list of links), so they count as
# modifiers, not prose.
PROSE_NOUNS = frozenset(
    {
        "summary", "information", "info", "experience", "comment", "comments",
        "note", "notes", "essay", "statement", "background", "about", "other",
        "anything", "else", "additional", "letter", "details", "context",
        "explanation",
    }
)
GENERIC_WORDS = frozenset(
    {"other", "else", "additional", "extra", "new", "another", "alternate",
     "alternative", "secondary", "more", "any", "current", "preferred"}
)
_LABEL_TRAIL = re.compile(
    r"(?:\s*[*\u2731:]+|\s*\((?:optional|required)\)\s*)+$", re.IGNORECASE
)


def _label_text(field: dict[str, Any]) -> str:
    """The field's label without its trailing marks ("*", ":", "(optional)")."""
    label = str(field.get("label") or "").strip()
    return _LABEL_TRAIL.sub("", label).strip()


def is_factual_box(field: dict[str, Any]) -> bool:
    """A textarea asking for a fact, not prose: a name, a link, a date, a yes/no.

    It goes to the short-answer pass; a box the fact pass leaves empty is still
    drafted, because is_long_form_field is unchanged.
    """
    if str(field.get("tag") or "").casefold() != "textarea":
        return False
    if is_sensitive_field(field):
        return False
    if NARRATIVE_QUESTION.search(_field_context(field)):
        return False
    question = _label_text(field)
    lowered = question.casefold()
    words = [re.sub(r"^[^\w]+|[^\w]+$", "", word) for word in question.split()]
    nouns = {word.casefold() for word in words if word}
    sentences = [part.strip() for part in re.split(r"[.?!]+", question) if part.strip()]
    if (
        len(words) <= 5
        and "?" not in question
        and nouns & FACT_NOUNS
        and not ((nouns & PROSE_NOUNS) - GENERIC_WORDS - FACT_NOUNS)
    ):
        return True
    if (
        len(sentences) == 1
        # A list of several asks ("the school, the degree, and the date") is prose.
        and "," not in question
        and re.match(
            r"^(?:please\s+)?(?:provide|list|enter|give|share|include)\s+"
            r"(?:your|any|the|a|all)\b",
            lowered,
        )
        and nouns & FACT_NOUNS
    ):
        return True
    if re.match(r"^(?:when\b|what dates?\b)", lowered) and (
        len(sentences) == 1
        or (
            len(sentences) == 2
            and re.match(r"^please\s+(?:provide|enter)\b", sentences[1].casefold())
        )
    ):
        return True
    if (
        question.endswith("?")
        and len(sentences) == 1
        and re.match(
            r"^(?:to your knowledge,?\s+)?"
            r"(?:do|does|did|are|is|have|has|can|will|would)\s+you\b",
            lowered,
        )
    ):
        return True
    return bool(
        re.match(
            r"^how did you (?:first )?(?:hear|find out|learn) about\b", lowered
        )
    )


def is_narrative_box(field: dict[str, Any]) -> bool:
    """A long-form box whose question asks for a story, which a learned
    expression never fills: only a binding or a draft does."""
    return is_long_form_field(field) and NARRATIVE_QUESTION.search(
        _field_context(field)
    ) is not None


def choice_group_key(field: dict[str, Any]) -> str:
    """Which choice group an option belongs to.

    Radios answer one question per `name` (HTML groups them that way), so two
    questions worded alike (Lever repeats a card) stay two groups. Checkboxes
    group by their question alone: JazzHR names each Yes/No box differently.
    """
    group = normalize_question(str(field.get("group") or ""))
    if str(field.get("type") or "").casefold() == "radio" and field.get("name"):
        return f"{group}|{field['name']}"
    return group


def offered_options(fields: list[dict[str, Any]], field: dict[str, Any]) -> list[str]:
    """The options a control's question offers: a choice group's labels, else its own."""
    if str(field.get("type") or "").casefold() not in {"radio", "checkbox"} or not field.get(
        "group"
    ):
        return [str(option) for option in field.get("options") or []]
    group = choice_group_key(field)
    return [
        str(item.get("label") or item.get("value") or "")
        for item in fields
        if str(item.get("type") or "").casefold() in {"radio", "checkbox"}
        and choice_group_key(item) == group
    ]


BUTTON_TYPES = frozenset({"submit", "button", "reset", "hidden"})


def follow_up_condition(question: str) -> bool | None:
    """The answer the question above needs for this one to apply: "If yes, ..." is True."""
    condition = CONDITIONAL.match(question)
    if condition is None:
        return None
    return (condition.group(1) or condition.group(2)).casefold() in {"yes", "so"}


def is_follow_up_box(field: dict[str, Any]) -> bool:
    """A box a follow-up is typed into; a choice or an upload is never answered N/A."""
    field_type = str(field.get("type") or "").casefold()
    return (
        field_type not in {"radio", "checkbox", "file", "yesno"}
        and str(field.get("tag") or "").casefold() != "select"
        and field.get("role") != "combobox"
    )


def _question_key(field: dict[str, Any]) -> str:
    if str(field.get("type") or "").casefold() in {"radio", "checkbox"}:
        return choice_group_key(field)
    return normalize_question(str(field.get("label") or ""))


def held_answer(field: dict[str, Any]) -> bool | None:
    """The yes or no a choice control already holds on the page, if it holds one."""
    field_type = str(field.get("type") or "").casefold()
    if field_type in {"radio", "checkbox"}:
        if field.get("filled") is not True:
            return None
        return polarity(str(field.get("label") or field.get("value") or ""))
    if field_type == "yesno" and field.get("value"):
        return polarity(str(field["value"]))
    if field.get("selected"):
        return polarity(str(field["selected"]))
    return None


def answer_above(fields: list[dict[str, Any]], target: dict[str, Any]) -> bool | None:
    """The yes or no the page holds for the question just above this control.

    None when that question is not a yes/no choice, is unanswered, or holds
    both (two Yes/No boxes ticked).
    """
    above: set[bool] = set()
    current: set[bool] = set()
    current_key: str | None = None
    frame = target.get("frame_index") or 0
    for field in fields:
        field_type = str(field.get("type") or "").casefold()
        if (field.get("frame_index") or 0) != frame or field_type in BUTTON_TYPES or (
            str(field.get("tag") or "").casefold() == "button"
        ):
            continue
        key = _question_key(field)
        if key != current_key:
            above, current, current_key = current, set(), key
        if field.get("index") == target.get("index"):
            return next(iter(above)) if len(above) == 1 else None
        held = held_answer(field)
        if held is not None:
            current.add(held)
    return None
