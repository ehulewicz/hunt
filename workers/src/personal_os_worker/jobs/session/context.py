"""The session's context, state, constants, and shared helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Page
from rich.markup import escape

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.config import console
from personal_os_worker.jobs.account import AccountFlow
from personal_os_worker.jobs.answer_check import (
    Judgement,
    check_answers,
)
from personal_os_worker.jobs.answers import draft_contextual_answer
from personal_os_worker.jobs.audit import (
    Budget,
    Finding,
)
from personal_os_worker.jobs.autofill import (
    AnswerResolution,
    is_factual_box,
    is_long_form_field,
    is_sensitive_field,
)
from personal_os_worker.jobs.browser import VisibleBrowser
from personal_os_worker.jobs.compose import Composed, LibraryText, compose
from personal_os_worker.jobs.cover_letter import (
    LetterSlots,
    LetterTemplate,
    draft_slots,
    letter_pdf,
    load_template,
    review_letter,
)
from personal_os_worker.jobs.fix.queue import FixQueue
from personal_os_worker.jobs.keys import NoKeys, Terminal
from personal_os_worker.jobs.options import (
    choose_option,
    normalize_question,
)
from personal_os_worker.jobs.page_state import TabNavigation
from personal_os_worker.jobs.registry.kinds import question_of
from personal_os_worker.jobs.remote import LinkedKeys, RunLink
from personal_os_worker.jobs.replay import Sighting
from personal_os_worker.jobs.resolve import Knowledge, Miss
from personal_os_worker.jobs.short_answers import ShortQuestion, Verdict, draft_short_answers


class SubmittedByUser(Exception):
    """The page's Submitted: the user says the application went in."""



class SkippedByUser(Exception):
    """The page's Skip: the user passes on this listing; it leaves the board unapplied."""


Drafter = Callable[..., str | None]
Composer = Callable[..., Composed]
ShortDrafter = Callable[..., dict[str, Verdict]]
AnswerChecker = Callable[..., dict[str, Judgement]]

LetterDrafter = Callable[..., LetterSlots | None]
LetterRenderer = Callable[..., Path]


def leave_letter_for_you(path: Path) -> None:
    """The default letter review: no page, no accept — left for the Hunt page."""
    review_letter(path, link=None, accept=False)


@dataclass
class SessionContext:
    api: WorkerApi
    run_id: str
    ats: str  # a label for the fill events and failures, never behavior
    browser: VisibleBrowser
    page: Page
    console_errors: list[str]
    knowledge: Knowledge
    resume_json: dict[str, Any]
    resume_pdf: Path
    company: str
    title: str
    description: str | None
    # The career record, so a fact set mid-application can rebuild what is known.
    career: dict[str, Any] | None = None
    # The narrative library, as the composer sees it; empty means the fallback
    # drafter writes every long-form answer.
    library: tuple[LibraryText, ...] = ()
    transcript_pdf: Path | None = None
    dry_run: bool = False
    drafter: Drafter = draft_contextual_answer
    composer: Composer = compose
    short_drafter: ShortDrafter = draft_short_answers
    # Orders facts by how close their labels are to each question, for the
    # short-answer pass; None runs it unranked.
    fact_ranker: Callable[[list[str], list[dict[str, str]]], dict[str, list[str]]] | None = None
    checker: AnswerChecker = check_answers
    letter_drafter: LetterDrafter = draft_slots
    letter_template: Callable[[], LetterTemplate] = load_template
    editor: Callable[[Path], None] = leave_letter_for_you
    letter_renderer: LetterRenderer = letter_pdf
    letter_dir: Path | None = None  # where the editable letter and its PDF live
    use_claude: bool = True
    # Retry a field that fails right here, on the live page (design R55).
    repair: bool = False
    # Print every control's whole decision; with `step`, pause before each fill.
    explain: bool = False
    step: bool = False
    # Ask for a missing fact's value at the terminal instead of only reporting it.
    ask_facts: bool = False
    # How many times the audit may look, repair, and look again on one step.
    audit_rounds: int = 2
    # Signs in past an ATS account wall; None without a contact.email fact.
    account: AccountFlow | None = None
    poll_seconds: float = 1.0
    keys: Terminal | NoKeys | LinkedKeys = field(default_factory=Terminal)
    prompt: Callable[[str], str] = input
    # This device's agent, when one is listening: the Hunt page sees and drives the run.
    link: RunLink | None = None
    # The fixer: a failure no lane without code settles gets a code fix, written
    # and tested in the background and kept at a safe point (`jobs/fix/`).
    fixes: FixQueue | None = None
    cdp_url: str = ""
    # `os hunt apply --submit`: this run presses Next and Submit itself (`jobs/submit.py`).
    submit: bool = False


@dataclass
class SessionState:
    review: list[tuple[str, str]] = field(default_factory=list)  # (question, source) long-form
    cover_letter: str | None = None  # the edited letter, once per run
    collected: dict[str, dict[str, Any]] = field(default_factory=dict)
    seeds: dict[str, tuple[str, str]] = field(default_factory=dict)  # question -> (text, source)
    planned: dict[str, str] = field(default_factory=dict)  # question -> planned value
    resolutions: dict[str, AnswerResolution] = field(default_factory=dict)
    announced: dict[str, str] = field(default_factory=dict)  # control key -> value shown
    observations: list[dict[str, Any]] = field(default_factory=list)
    persisted_observations: int = 0
    warnings: set[str] = field(default_factory=set)
    tier_answers: dict[str, str] = field(default_factory=dict)  # question -> pattern/model answer
    tier_source: dict[str, str] = field(default_factory=dict)  # question -> "pattern" | "model"
    tier_resolver: dict[str, str] = field(default_factory=dict)
    tier_fact: dict[str, str] = field(default_factory=dict)  # question -> fact it came from
    seed_resolver: dict[str, str] = field(default_factory=dict)  # question -> prose/model id
    asked_claude: dict[str, int] = field(default_factory=dict)  # question -> times asked
    # A prose question is drafted at most once a run, whatever else is asked.
    drafted: set[str] = field(default_factory=set)
    judged: set[str] = field(default_factory=set)  # answers already checked against the facts
    check_findings: dict[str, Finding] = field(default_factory=dict)
    model_error: str | None = None
    repair_attempted: set[str] = field(default_factory=set)  # questions repair already retried
    budget: Budget = field(default_factory=Budget)
    misses: dict[str, Miss] = field(default_factory=dict)  # answer key -> why unanswered
    needed: dict[str, str] = field(default_factory=dict)  # question -> fact the model named
    # Questions a none fact leaves blank on purpose: question -> that fact.
    blanks: dict[str, str] = field(default_factory=dict)
    # A choice you made that a fact contradicts: control key -> the value, never saved.
    withheld: dict[str, str] = field(default_factory=dict)
    na_flagged: set[str] = field(default_factory=set)  # N/A follow-ups that now apply
    outlined: set[str] = field(default_factory=set)  # questions the audit left for you
    gates_tried: set[str] = field(default_factory=set)  # gate questions already answered once
    gate_pending: dict[str, Any] | None = None  # a gate left for you, watched for your click
    account_tried: set[str] = field(default_factory=set)  # account page URLs already tried
    account_parked: bool = False  # the account flow gave the wall to you; it stays yours
    step: int = 0
    form_seen: bool = False
    form_site: str | None = None  # the registrable site the form was first seen on
    navigation: TabNavigation | None = None
    # What stood between the run and its form, this run: the fixer's to work on.
    walls: list[Finding] = field(default_factory=list)
    # (cause, question) already stored as a failure issue this run.
    persisted_gaps: set[tuple[str, str]] = field(default_factory=set)
    # A problem's key -> the failure issue it was stored as, for the fixer.
    issue_ids: dict[str, str] = field(default_factory=dict)
    # A missing fact's question -> its issue, which the fact given for it closes.
    # Not `issue_ids`: gaps on identical widgets share one problem key there.
    gap_issues: dict[str, str] = field(default_factory=dict)
    # Problems handed to the fixer this run, kept or not: never handed over twice.
    fix_keys: set[str] = field(default_factory=set)
    # The accepted answers a learned change is replayed against, read once a run;
    # None when they could not be read.
    sightings: list[Sighting] | None = None
    sightings_read: bool = False
    # (question, fact) -> why a lesson waits in the Inbox: it is saved once a run.
    held: dict[tuple[str, str], str] = field(default_factory=dict)
    similar_prose: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Model calls by purpose made by the processes this run reloaded from.
    # A press that may have sent the application: a run that ends now is unconfirmed.
    submit_pressed: bool = False
    pressed_step: int | None = None  # the last step a `--submit` run pressed forward on
    # Model calls by purpose made by the processes this run reloaded from.


# Open causes stored as failure issues by the audit itself.
PERSISTED_CAUSES = frozenset({"fact_gap", "unknown_control", "detection_error"})
# Causes only code can settle once the lanes without code have had their turn.
CODE_CAUSES = frozenset({
    "unknown_control", "detection_error", "control_error", "format_error", "wall",
    "posting_not_found",
})
# What already ran before a cause reached the fixer, in the words its prompt uses.
TRIED: dict[str, tuple[str, ...]] = {
    "control_error": ("the repair lane tried every strategy this control's kind has",),
    "format_error": ("the repair lane tried every other rendering of the fact",),
    "unknown_control": ("no control kind captures this widget, so nothing could operate it",),
    "detection_error": ("the question extraction found no question for these options",),
    "wall": ("the one-click Apply control, gate answering by fact, and the account flow",),
    "posting_not_found": ("a link naming the role, the site's search by the full and "
                          "shorter titles, and its open roles",),
}


def page_signature(page: Page, fields: list[dict[str, Any]]) -> tuple[str, tuple[str, ...]]:
    return page.url, tuple(str(item.get("fingerprint")) for item in fields)


def preview(value: str, *, limit: int = 100) -> str:
    """One terminal line of a value, however long the textarea answer is."""
    flat = " ".join(value.split())
    return flat if len(flat) <= limit else flat[: limit - 3].rstrip() + "..."


# A build that shells out can fail three ways: a bad exit (RuntimeError), no
# executable (FileNotFoundError, an OSError), or the 120 s cap
# (subprocess.TimeoutExpired, a SubprocessError). None of them is a DOM fault.
ARTIFACT_BUILD_ERRORS = (RuntimeError, OSError, subprocess.SubprocessError)


def error_note(error: BaseException, *, limit: int = 200) -> str:
    """The informative line of an exception message, not a tool's startup banner."""
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    text = lines[-1] if lines else error.__class__.__name__
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


TAG_WIDTH = 10
QUESTION_WIDTH = 42
VALUE_WIDTH = 60

# How many times one question may be put to the model in a run: the fill pass,
# and the audit once more after a repair or a new fact has changed something.
ASK_LIMIT = 2
# What one repair attempt costs the question's budget. A retry walks a control's
# strategies, or a fact's renderings across them, so it is never one operation.
REPAIR_COST = 5

# Passes over questions an answer revealed, before the page is left to you.
REVEAL_PASSES = 2

TAG_STYLES = {
    "pattern": "green",
    "learned": "magenta",
    "source": "dim",
    "consent": "dim",
    "long-form": "cyan",
    "prose": "cyan",
    "letter": "cyan",
    "picked up": "cyan",
    "saved": "green",
    "fact": "green",
    "kept": "dim",
    "blank": "dim",
    "left": "yellow",
    "optional": "dim",
    "review": "yellow",
    "failed": "red",
    "repaired": "green",
}


def echo(tag: str, question: str, value: str | None = None, *, note: str | None = None) -> None:
    """One aligned line: `  tag  question  -> value  (note)`."""
    style = TAG_STYLES.get(tag, "")
    label = f"[{style}]{tag:<{TAG_WIDTH}}[/{style}]" if style else f"{tag:<{TAG_WIDTH}}"
    text = preview(question, limit=QUESTION_WIDTH)
    line = f"  {label} {escape(text):<{QUESTION_WIDTH}}"
    if value is not None:
        line += f" -> {escape(preview(value, limit=VALUE_WIDTH))}"
    if note:
        line += f"  [dim]({escape(note)})[/dim]"
    console.print(line)


def _question_for(field: dict[str, Any]) -> str:
    """The question a control answers: its group for a choice control, else its label."""
    return question_of(field)



def member_control(
    fields: list[dict[str, Any]], field: dict[str, Any], value: str
) -> dict[str, Any] | None:
    """The control a value should operate: a choice group's matching option, else the field.

    A group's question is answered by one of its options, and `find_field`
    returns whichever option came first. Operating that one would check the
    wrong box. A checkbox group is left alone: correcting it means unchecking
    what is already ticked, which is yours to do.
    """
    field_type = str(field.get("type") or "").casefold()
    if field_type not in {"radio", "checkbox"}:
        return field
    group = normalize_question(str(field.get("group") or ""))
    members = [
        item
        for item in fields
        if str(item.get("type") or "").casefold() in {"radio", "checkbox"}
        and normalize_question(str(item.get("group") or "")) == group
    ]
    if field_type == "checkbox" and len(members) > 1:
        return None
    labels = [str(item.get("label") or item.get("value") or "") for item in members or [field]]
    chosen = choose_option(labels, value)
    if chosen is None:
        return None
    return (members or [field])[labels.index(chosen)]


def _claude_questions(fields: list[dict[str, Any]]) -> list[ShortQuestion]:
    """Unfilled, non-sensitive, non-prose, non-file controls left for Claude.

    Radio/checkbox groups with no option checked (options = the group's option
    labels), selects and typeaheads with a known menu (their options), and
    all text inputs, required or optional (no options).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    questions: dict[str, ShortQuestion] = {}
    for item in fields:
        field_type = str(item.get("type") or "").casefold()
        tag = str(item.get("tag") or "").casefold()
        if field_type in {"file", "submit", "button", "reset", "hidden"} or tag == "button":
            continue
        if is_sensitive_field(item) or (
            is_long_form_field(item) and not is_factual_box(item)
        ):
            continue
        if field_type in {"radio", "checkbox"}:
            group = str(item.get("group") or "").strip()
            if group:
                groups.setdefault(group, []).append(item)
            continue
        if item.get("filled") is True:
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        if tag == "select" or item.get("options"):
            questions[label] = ShortQuestion(
                label, tuple(str(option) for option in item.get("options") or [])
            )
        else:
            questions[label] = ShortQuestion(label, ())
    for group, members in groups.items():
        if any(member.get("filled") is True for member in members):
            continue
        options = tuple(
            str(member.get("label") or member.get("value") or "") for member in members
        )
        questions[group] = ShortQuestion(group, options)
    return list(questions.values())


def content_type_for(name: str) -> str:
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".html"):
        return "text/html"
    return "application/json"
