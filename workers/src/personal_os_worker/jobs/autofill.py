from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Protocol

from personal_os_worker.jobs.autofill_fields import (
    BUTTON_TYPES,
    NARRATIVE_QUESTION,
    answer_above,
    choice_group_key,
    follow_up_condition,
    held_answer,
    is_consent_field,
    is_cover_letter_field,
    is_factual_box,
    is_follow_up_box,
    is_long_form_field,
    is_narrative_box,
    is_resume_field,
    is_sensitive_field,
    is_signature_field,
    is_transcript_field,
    offered_options,
)
from personal_os_worker.jobs.options import (
    choose_option,
    normalize_question,
    same_name,
)
from personal_os_worker.jobs.registry.formats import BLANK, LIVE_MENU
from personal_os_worker.jobs.registry.formats.choices import polarity
from personal_os_worker.jobs.sites import is_non_answer_option

__all__ = [
    "BLANK_KIND",
    "FILE_KINDS",
    "MULTI",
    "NARRATIVE_QUESTION",
    "NOT_APPLICABLE",
    "SIGNATURE_FACT",
    "AnswerResolution",
    "AutofillPlan",
    "FormFill",
    "Resolves",
    "answer_above",
    "build_autofill_plan",
    "choice_group_key",
    "follow_up_condition",
    "is_consent_field",
    "is_cover_letter_field",
    "is_factual_box",
    "is_follow_up_box",
    "is_long_form_field",
    "is_narrative_box",
    "is_resume_field",
    "is_sensitive_field",
    "is_signature_field",
    "normalize_question",
    "offered_options",
    "signature_resolution",
]

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


# What the engine writes into a follow-up the answer above rules out.
NOT_APPLICABLE = AnswerResolution("N/A", "pattern", "engine:not_applicable")
# The fact the engine signs a typed signature with.
SIGNATURE_FACT = "identity.legal_name"


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

    group_options, filled_groups = _choice_groups(fields)

    for field in fields:
        above, current, current_key = _plan_field(
            field, resolver, referred, resume_candidates, transcript_candidates,
            group_options, filled_groups, above, current, current_key,
            blank_groups, answered_groups, pending_choices, fills, unanswered, reasons,
        )

    # A choice group counts as answered once any of its options is planned.
    unanswered.extend(
        label for group_key, label in pending_choices if group_key not in answered_groups
    )
    return AutofillPlan(tuple(fills), tuple(dict.fromkeys(unanswered)), reasons)


def _choice_groups(
    fields: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], set[str]]:
    """Each choice group's option labels, and the groups the page already answers."""
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
    return group_options, filled_groups


def _plan_field(
    field: dict[str, Any],
    resolver: Resolves | None,
    referred: bool,
    resume_candidates: set[int],
    transcript_candidates: set[int],
    group_options: dict[str, list[str]],
    filled_groups: set[str],
    above: set[bool],
    current: set[bool],
    current_key: str | None,
    blank_groups: set[str],
    answered_groups: set[str],
    pending_choices: list[tuple[str, str]],
    fills: list[FormFill],
    unanswered: list[str],
    reasons: dict[int, str],
) -> tuple[set[bool], set[bool], str | None]:
    """Plan one field into the accumulators; return the follow-up tracker it leaves."""
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
        return above, current, current_key
    if field_type == "radio" and not group:
        reasons[index] = "question label missing: extraction needs repair"
        return above, current, current_key
    if normalized != current_key:
        above, current, current_key = current, set(), normalized
    held = held_answer(field)
    if held is not None:
        current.add(held)
    if field.get("filled") is True:
        reasons[index] = "already has a value: left as it is"
        return above, current, current_key
    if field_type in {"radio", "checkbox"} and normalized in filled_groups:
        reasons[index] = "its group already has a choice: left as it is"
        return above, current, current_key
    if field_type in {"radio", "checkbox"} and normalized in blank_groups:
        reasons[index] = "its group stays blank on purpose"
        return above, current, current_key

    kind, value, chooser, resolution = _plan_value(
        field, question_label, field_type, normalized, index, sensitive, resolver,
        resume_candidates, transcript_candidates, group_options, blank_groups, reasons,
    )
    condition = follow_up_condition(question_label)
    if (
        value is None and chooser is None and kind != BLANK_KIND and condition is not None
        and is_follow_up_box(field) and not is_signature_field(field)
        and len(above) == 1 and condition not in above
    ):
        # "If you answered No to the above, ..." after a Yes: it does not apply,
        # required or not.
        resolution = NOT_APPLICABLE
        value = resolution.value
        reasons[index] = (
            f"engine: the question above was answered {'No' if condition else 'Yes'}, "
            "so this follow-up does not apply"
        )
    _record_decision(
        field, label, field_type, group, question_label, normalized, index, required,
        kind, value, chooser, resolution, referred,
        current, answered_groups, pending_choices, fills, unanswered,
    )
    return above, current, current_key


def _plan_value(
    field: dict[str, Any],
    question_label: str,
    field_type: str,
    normalized: str,
    index: int,
    sensitive: bool,
    resolver: Resolves | None,
    resume_candidates: set[int],
    transcript_candidates: set[int],
    group_options: dict[str, list[str]],
    blank_groups: set[str],
    reasons: dict[int, str],
) -> tuple[str, str | None, str | None, AnswerResolution | None]:
    """The kind, value, chooser and resolution the engine or the resolver gives a field."""
    value: str | None = None
    chooser: str | None = None
    resolution: AnswerResolution | None = None
    kind = "text"
    # Consent is answered like any question, by the pattern that names
    # `consent.agrees`; a typed signature is signed with your legal name.
    if is_signature_field(field):
        resolution = signature_resolution(field, question_label, resolver)
        value = resolution.value if resolution is not None else None
        reasons[index] = (
            f"engine: signed with {SIGNATURE_FACT}" if resolution is not None
            else f"a signature, and no {SIGNATURE_FACT} to sign with: left for you"
        )
    elif sensitive and (field_type == "file" or not is_consent_field(field)):
        reasons[index] = "sensitive (a signature to check, a consent upload): left for you"
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
        kind, value, chooser, resolution = _resolve_value(
            field, question_label, field_type, normalized, index, resolver,
            group_options, blank_groups, reasons,
        )
    return kind, value, chooser, resolution


def signature_resolution(
    field: dict[str, Any], question: str, resolver: Resolves | None
) -> AnswerResolution | None:
    """Your legal name rendered for a typed signature box, or None without the fact."""
    render_fact = getattr(resolver, "render_fact", None)
    rendering = render_fact(SIGNATURE_FACT, field, question) if render_fact else None
    if rendering is None or rendering.formatter == BLANK or rendering.formatter.startswith(
        "none_as"
    ):
        return None
    return AnswerResolution(
        rendering.value, "pattern", "engine:signature",
        fact_key=SIGNATURE_FACT, formatter=rendering.formatter,
    )


def _resolve_value(
    field: dict[str, Any],
    question_label: str,
    field_type: str,
    normalized: str,
    index: int,
    resolver: Resolves | None,
    group_options: dict[str, list[str]],
    blank_groups: set[str],
    reasons: dict[int, str],
) -> tuple[str, str | None, str | None, AnswerResolution | None]:
    """What the resolver answers for a field, fitted to the options it offers."""
    chooser: str | None = None
    resolution: AnswerResolution | None = None
    kind = "text"
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
        value = _match_group_option(
            field, field_type, normalized, index, value, group_options, reasons
        )
    return kind, value, chooser, resolution


def _match_group_option(
    field: dict[str, Any],
    field_type: str,
    normalized: str,
    index: int,
    value: str,
    group_options: dict[str, list[str]],
    reasons: dict[int, str],
) -> str | None:
    """The answer when it names this option of its choice group, else None."""
    own = str(field.get("label") or field.get("value") or "")
    options = group_options.get(normalized) or [own]
    if field_type == "checkbox" and len(options) > 1 and (
        MULTI in value or "," in value
    ):
        # Several answers to a checkbox group: check every option named,
        # by name, never by prefix. A newline separates chosen options
        # (which may hold commas); a comma separates typed names.
        parts = value.split(MULTI) if MULTI in value else value.split(",")
        matched = own if any(
            part.strip() == own.strip() or same_name(own, part) for part in parts
        ) else None
        if matched is None:
            reasons[index] += f"; this box is not among {parts!r}"
        return matched
    chosen = choose_option(options, value)
    if chosen is None:
        reasons[index] += f"; no option matches {value!r}"
    elif chosen != own:
        reasons[index] += f"; the group's choice is {chosen!r}, not this option"
    if chosen is None or chosen != own:
        return None
    return value


def _record_decision(
    field: dict[str, Any],
    label: str,
    field_type: str,
    group: str,
    question_label: str,
    normalized: str,
    index: int,
    required: bool,
    kind: str,
    value: str | None,
    chooser: str | None,
    resolution: AnswerResolution | None,
    referred: bool,
    current: set[bool],
    answered_groups: set[str],
    pending_choices: list[tuple[str, str]],
    fills: list[FormFill],
    unanswered: list[str],
) -> None:
    """Add the field's fill, or the required question it leaves open."""
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
