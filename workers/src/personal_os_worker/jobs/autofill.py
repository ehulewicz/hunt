from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Any, Protocol

from personal_os_worker.jobs.formatters import BLANK, LIVE_MENU, polarity
from personal_os_worker.jobs.options import (
    choose_option,
    normalize_question,
    same_name,
)
from personal_os_worker.jobs.sites import is_non_answer_option

FILE_KINDS = frozenset({"resume", "transcript"})
# A question a none fact answers: nothing is operated, and it stays blank on purpose.
BLANK_KIND = "blank"
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
    """Anything that answers a field: active patterns, or the model's verdicts."""

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
    # This application came through a referral (`apply --referred`): a source
    # chosen against the live menu prefers the referral option.
    referred: bool = False


@dataclass(frozen=True, slots=True)
class AutofillPlan:
    fills: tuple[FormFill, ...]
    unanswered_required_questions: tuple[str, ...]
    # Control index -> why the planner filled it or left it, for `--explain`.
    reasons: dict[int, str] = dataclasses.field(default_factory=dict)


# Consent, terms, and attestation controls are recognised here so they are never
# drafted or put to a model; the pattern naming `consent.agrees` answers them
# (R68), and only a signature is left to the user. Demographic questions are
# ordinary saved answers for a single user and are not blocked.
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
# What the engine writes into a follow-up the answer above rules out.
NOT_APPLICABLE = AnswerResolution("N/A", "pattern", "engine:not_applicable")
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


def build_autofill_plan(
    fields: list[dict[str, Any]],
    *,
    resolver: Resolves | None = None,
) -> AutofillPlan:
    """Plan fills: the engine's own rules, then whatever the resolver answers from facts.

    The engine handles the resume and transcript uploads and the N/A of a
    follow-up the answer above rules out; every other value, consent and "how
    did you hear" included, comes from the resolver. Choice
    groups pick the single option that equals, starts with, is a prefix of, or
    contains every word of the answer.
    """
    fills: list[FormFill] = []
    unanswered: list[str] = []
    reasons: dict[int, str] = {}
    # Choice groups a none fact leaves blank: one blank fill answers the group.
    blank_groups: set[str] = set()
    knowledge = getattr(resolver, "knowledge", None)
    referred = bool(knowledge is not None and knowledge.facts.get("referral.referred"))
    # The yes or no each question holds or is planned to hold, for the follow-up
    # just below it: the one above, and the one being read now.
    above: set[bool] = set()
    current: set[bool] = set()
    current_key: str | None = None
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
            group_key = choice_group_key(field)
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
        normalized = choice_group_key(field) if field_type in {"radio", "checkbox"} else (
            normalize_question(question_label)
        )
        tag = str(field.get("tag") or "").casefold()
        sensitive = is_sensitive_field(field)
        required = field.get("required") is True
        index = int(field["index"])

        if field_type in BUTTON_TYPES or tag == "button":
            reasons[index] = "a button: never operated"
            continue
        if field_type == "radio" and not group:
            reasons[index] = "question label missing: extraction needs repair"
            continue
        if normalized != current_key:
            above, current, current_key = current, set(), normalized
        held = held_answer(field)
        if held is not None:
            current.add(held)
        if field.get("filled") is True:
            reasons[index] = "already has a value: left as it is"
            continue
        if field_type in {"radio", "checkbox"} and normalized in filled_groups:
            reasons[index] = "its group already has a choice: left as it is"
            continue
        if field_type in {"radio", "checkbox"} and normalized in blank_groups:
            reasons[index] = "its group stays blank on purpose"
            continue

        value: str | None = None
        chooser: str | None = None
        resolution: AnswerResolution | None = None
        kind = "text"
        # Consent is answered like any question, by the pattern that names
        # `consent.agrees`; a signature or a password is yours.
        if sensitive and (field_type == "file" or not is_consent_field(field)):
            reasons[index] = "sensitive (a signature, a password): left for you"
        elif field_type == "file" and index in resume_candidates and len(resume_candidates) == 1:
            kind = "resume"
            reasons[index] = "engine: the one resume upload"
        elif (
            field_type == "file"
            and index in transcript_candidates
            and len(transcript_candidates) == 1
        ):
            kind = "transcript"
            reasons[index] = "engine: the one transcript upload"
        elif field_type == "file":
            reasons[index] = "a file no rule names (or one of several): left for you"
        else:
            # A checkbox with no group offers itself as its one option.
            own = str(field.get("label") or field.get("value") or "")
            options = (
                group_options.get(normalized) or ([own] if field_type == "checkbox" else [])
                if field_type in {"radio", "checkbox"}
                else [str(option) for option in field.get("options") or []]
            )
            if resolver is not None:
                resolution = resolver.resolve(
                    dict(field) | {"label": question_label, "options": options}
                )
                reasons[index] = (
                    f"resolver: {resolution.resolver_id}" if resolution is not None
                    else "resolver: nothing answered"
                )
            else:
                reasons[index] = "no resolver in this pass"
            # A value that declines to answer is never chosen, whoever proposed it.
            if resolution is not None and is_non_answer_option(resolution.value):
                reasons[index] = f"{resolution.value!r} declines to answer: never chosen"
                resolution = None
            if resolution is not None and resolution.formatter == BLANK:
                kind = BLANK_KIND
                reasons[index] += f"; {resolution.fact_key} is none, so it stays blank on purpose"
                if field_type in {"radio", "checkbox"}:
                    blank_groups.add(normalized)
            value = (
                resolution.value if resolution is not None and kind != BLANK_KIND else None
            )
            if (
                resolution is not None and resolution.fact_key in LIVE_MENU and not options
                and field.get("role") == "combobox"
            ):
                # A typeahead nobody has opened: the agreeing option, or your
                # standing source, is chosen against its live menu.
                chooser = LIVE_MENU[resolution.fact_key]
                value = None
                reasons[index] += "; chosen against the live menu"

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
                    if value is None:
                        reasons[index] += f"; this box is not among {parts!r}"
                else:
                    chosen = choose_option(options, value)
                    if chosen is None:
                        reasons[index] += f"; no option matches {value!r}"
                    elif chosen != own:
                        reasons[index] += f"; the group's choice is {chosen!r}, not this option"
                    if chosen is None or chosen != own:
                        value = None

        condition = follow_up_condition(question_label)
        if (
            value is None and chooser is None and kind != BLANK_KIND and condition is not None
            and is_follow_up_box(field) and len(above) == 1 and condition not in above
        ):
            # "If you answered No to the above, ..." after a Yes: it does not apply,
            # required or not.
            resolution = NOT_APPLICABLE
            value = resolution.value
            reasons[index] = (
                f"engine: the question above was answered {'No' if condition else 'Yes'}, "
                "so this follow-up does not apply"
            )
        if kind in FILE_KINDS or kind == BLANK_KIND or value is not None or chooser is not None:
            fills.append(FormFill(
                index=index, kind=kind, value=value, chooser=chooser, resolution=resolution,
                referred=referred,
            ))
            if value is not None and not is_follow_up_box(field):
                answer = polarity(value)
                if answer is not None:
                    current.add(answer)
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
    return AutofillPlan(tuple(fills), tuple(dict.fromkeys(unanswered)), reasons)
