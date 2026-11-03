"""Manual completion, capture with each long-form answer's provenance, and advance."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any, Literal

import httpx
from playwright.sync_api import Error

from personal_os_worker.core.config import console
from personal_os_worker.jobs.autofill import (
    MULTI,
    NOT_APPLICABLE,
    AnswerResolution,
    answer_above,
    follow_up_condition,
    is_follow_up_box,
    is_sensitive_field,
)
from personal_os_worker.jobs.browser import (
    has_submit_control,
    highlight_unfilled,
    inspect_fields,
    snapshot_entries,
)
from personal_os_worker.jobs.options import (
    choose_option,
    normalize_answer_question,
)
from personal_os_worker.jobs.page_state import (
    ApplicationPageState,
    classify_application_page,
)
from personal_os_worker.jobs.registry.formats.choices import polarity
from personal_os_worker.jobs.repair.repairer import find_field
from personal_os_worker.jobs.resolve import Resolver
from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.context import (
    _question_for,
    echo,
    page_signature,
)

Source = Literal["model", "previous"]
# A shared stretch shorter than this is common words, not borrowed text.
MIN_SPAN = 16


def spans(final: str, *, draft: str = "", previous: Sequence[str] = ()) -> list[dict[str, object]]:
    """Ordered, non-overlapping spans of `final`, each with where it came from."""
    marks: list[Source | None] = [None] * len(final)
    sources: list[tuple[Source, str]] = [("previous", text) for text in previous if text]
    if draft:
        sources.append(("model", draft))
    for source, text in sources:
        matcher = SequenceMatcher(None, final, text, autojunk=False)
        for block in matcher.get_matching_blocks():
            if block.size < MIN_SPAN:
                continue
            for index in range(block.a, block.a + block.size):
                if marks[index] is None:
                    marks[index] = source
    found: list[dict[str, object]] = []
    start = 0
    for index in range(1, len(final) + 1):
        if index == len(final) or marks[index] != marks[start]:
            if marks[start] is not None:
                found.append({"start": start, "end": index, "source": marks[start]})
            start = index
    return found


class CaptureMixin(SessionBase):
    # ----- manual completion, capture, and advance --------------------------

    def contradiction(
        self, fields: list[dict[str, Any]], question: str, value: str
    ) -> tuple[str, str] | None:
        """The fact a choice on the page disagrees with, and the option it says.

        Only a choice is judged, since a click can land by accident and typing
        cannot. The fact is the one an active pattern names, else the one the
        worker filled this question from. None when no fact speaks to it.
        """
        field = find_field(fields, question)
        if field is None or is_follow_up_box(field) or is_sensitive_field(field):
            return None
        options = self.options_for(fields, field)
        target = dict(field) | {"label": question, "options": options}
        resolution = Resolver(self.ctx.knowledge).resolve(target)
        key = self.state.tier_fact.get(question)
        if resolution is None and key is not None:
            rendering = Resolver(self.ctx.knowledge).render_fact(key, target, question)
            if rendering is not None:
                resolution = AnswerResolution(
                    rendering.value, "pattern", "fact", fact_key=key,
                    formatter=rendering.formatter,
                )
        if resolution is None or resolution.fact_key is None or MULTI in resolution.value:
            return None
        wanted = choose_option(options, resolution.value) if options else None
        if wanted is not None:
            same = normalize_answer_question(wanted) == normalize_answer_question(value)
            return None if same else (resolution.fact_key, wanted)
        held, expected = polarity(value), polarity(resolution.value)
        if held is not None and expected is not None and held != expected:
            return resolution.fact_key, resolution.value
        return None

    def flag_follow_ups(self, fields: list[dict[str, Any]], held: dict[str, str]) -> None:
        """Say when an answer you changed makes a follow-up the worker answered N/A apply."""
        flagged: set[str] = set()
        for item in fields:
            question = _question_for(item)
            condition = follow_up_condition(question)
            if (
                condition is None
                or not is_follow_up_box(item)
                or self.state.tier_resolver.get(question) != NOT_APPLICABLE.resolver_id
                or held.get(question) != NOT_APPLICABLE.value
                or answer_above(fields, item) != condition
            ):
                continue
            flagged.add(question)
            if question not in self.state.na_flagged:
                echo("review", question, NOT_APPLICABLE.value,
                     note=f"you answered {'Yes' if condition else 'No'} above, so this applies "
                          "now: replace N/A")
        self.state.na_flagged = flagged

    def record_snapshot(self, fields: list[dict[str, Any]]) -> None:
        writing = False  # a long-form answer changed: saved at once, not at the run's end
        withheld = dict(self.state.withheld)
        flagged = self.state.na_flagged
        held: dict[str, str] = {}  # question -> what the page shows for it
        contradicted: set[str] = set()
        for index, item in enumerate(snapshot_entries(self.page, fields)):
            question = str(item["question"])
            value = str(item["answer_text"])
            if value:
                held[question] = value.strip()
            seed, seed_source = self.state.seeds.get(question, ("", "empty"))
            if question in self.state.tier_answers and value == self.state.tier_answers[question]:
                source = self.state.tier_source[question]
            elif seed_source in {"prose", "model"} and value == seed.strip():
                source = seed_source
            else:
                source = "manual"
            fingerprint = str(item.get("field_fingerprint") or f"control-{index}")
            control_key = f"{self.state.step}:{fingerprint}"
            previous = self.state.collected.get(control_key)
            if value and source == "manual":
                # A choice a fact contradicts is said at once and never saved:
                # it is most often a stray click. Put it back, or set the fact.
                if self.state.withheld.get(control_key) == value:
                    contradicted.add(question)
                    continue
                self.state.withheld.pop(control_key, None)
                against = self.contradiction(fields, question, value)
                if against is not None:
                    self.state.withheld[control_key] = value
                    contradicted.add(question)
                    fact_key, says = against
                    echo("review", question, value,
                         note=f"{fact_key} says {says}; not saved. Put it back, or set "
                              "the fact if this is right")
                    value = ""  # the page no longer holds an answer worth saving
            else:
                self.state.withheld.pop(control_key, None)
            if not value:
                self._forget_answer(control_key, previous)
                continue
            source, resolver_id, proposed_value = self._proposal(
                question, value, source, seed, control_key, previous
            )
            writing |= self._collect_answer(
                item, question, value, source, fingerprint, control_key, previous,
                resolver_id, proposed_value,
            )
        if writing and not self.ctx.dry_run:
            # Writing is kept as it is written: a run that is killed loses none of it.
            with contextlib.suppress(httpx.HTTPError, RuntimeError):
                self._save_now(quiet=True)
        self.flag_follow_ups(fields, held)
        if self.state.withheld != withheld or self.state.na_flagged != flagged:
            with contextlib.suppress(Error):
                highlight_unfilled(
                    self.page, fields, self.state.outlined | self.state.na_flagged | contradicted
                )

    def _forget_answer(self, control_key: str, previous: dict[str, Any] | None) -> None:
        """Drop a collected answer the page no longer shows, logging it as cleared by you."""
        if previous is not None:
            self.state.collected.pop(control_key)
            event = {**previous, "event_index": len(self.state.observations),
                     "page_url": self.page.url, "answer_text": "", "source": "manual"}
            self.state.observations.append(event)

    def _proposal(
        self,
        question: str,
        value: str,
        source: str,
        seed: str,
        control_key: str,
        previous: dict[str, Any] | None,
    ) -> tuple[str, str | None, str | None]:
        """The answer's source, the resolver that proposed it, and the value it proposed."""
        proposed_value = (
            self.state.tier_answers.get(question)
            or self.state.planned.get(question)
            or seed
            or None
        )
        if source in {"pattern", "model"} and question in self.state.tier_resolver:
            resolver_id: str | None = self.state.tier_resolver[question]
        elif source in {"prose", "model"}:
            resolver_id = self.state.seed_resolver.get(question) or "model:long_form_v1"
        else:
            resolver_id = previous.get("resolver_id") if previous else None
            if previous:
                proposed_value = previous.get("proposed_value")
        resolution = self.state.resolutions.get(control_key)
        if resolution is not None:
            source = resolution.source if value == resolution.value else "manual"
            resolver_id = resolution.resolver_id
            proposed_value = resolution.value
        return source, resolver_id, proposed_value

    def _collect_answer(
        self,
        item: dict[str, Any],
        question: str,
        value: str,
        source: str,
        fingerprint: str,
        control_key: str,
        previous: dict[str, Any] | None,
        resolver_id: str | None,
        proposed_value: str | None,
    ) -> bool:
        """Collect the answer, log it when it changed, and echo yours; True for new prose."""
        writing = False
        self.state.collected[control_key] = {
            "question": question,
            "answer_text": value,
            "source": source,
            "form": item.get("form") or "short",
            "step_number": self.state.step,
            "page_url": self.page.url,
            "control_kind": str(item["control_kind"]),
            "field_fingerprint": fingerprint,
            "resolver_id": resolver_id,
            "proposed_value": proposed_value,
        }
        origin = self.state.origins.get(question)
        if item.get("form") == "long" and origin is not None:
            draft, started_from = origin
            self.state.collected[control_key]["provenance"] = spans(
                value, draft=draft, previous=started_from
            )
        if previous is None or previous["answer_text"] != value:
            self.state.observations.append(
                {
                    "event_index": len(self.state.observations),
                    "step_number": self.state.step,
                    "page_url": self.page.url,
                    "question": question,
                    "answer_text": value,
                    "source": source,
                    "form": item.get("form") or "short",
                    "control_kind": str(item["control_kind"]),
                    "field_fingerprint": fingerprint,
                    "resolver_id": resolver_id,
                    "proposed_value": proposed_value,
                }
                | (
                    {"provenance": self.state.collected[control_key]["provenance"]}
                    if "provenance" in self.state.collected[control_key] else {}
                )
            )
            writing |= item.get("form") == "long"
        # Echo what the user typed or chose so they can see it was picked up.
        if source == "manual" and self.state.announced.get(control_key) != value:
            self.state.announced[control_key] = value
            echo("picked up", question, value)
        return writing

    def wait_and_advance(self, fields: list[dict[str, Any]]) -> ApplicationPageState:
        """Collect manual answers while the user owns page navigation and submission."""
        prior = page_signature(self.page, fields)
        submit_reported = False
        transition_since: float | None = None
        overview_active = False
        while True:
            self.page = self.ctx.browser.active_page(self.page)
            current = inspect_fields(self.page)
            page_state = classify_application_page(self.page, current, self.state.navigation)
            if page_state == ApplicationPageState.SUBMITTED:
                return page_state
            if page_state == ApplicationPageState.OVERVIEW:
                overview_active = True
                transition_since = None
                self.warn_once(
                    "overview-standby",
                    "Ashby Overview is open; standing by until you return to Application.",
                )
                self.sleep()
                continue
            current_signature = page_signature(self.page, current)
            if page_state == ApplicationPageState.FORM and overview_active:
                # Returning from the saved Overview tab resumes this same step.
                prior = current_signature
                overview_active = False
                transition_since = None
            elif page_state == ApplicationPageState.FORM and current_signature == prior:
                transition_since = None
            else:
                transition_since = transition_since or time.monotonic()
                if time.monotonic() - transition_since >= 5:
                    if page_state == ApplicationPageState.FORM:
                        return page_state
                    # A wall or a gate after the first page is the next step's
                    # to pass, as it was before the form.
                    if page_state == ApplicationPageState.ACCOUNT or self.gate_in_front():
                        return page_state
                    # The foreground tab is not the form: the user switched
                    # tabs or the page is mid-navigation. Only the user ends a
                    # session, so stand by until a form is in front again.
                    self.warn_once(
                        "form-standby",
                        "The application form is not in the foreground; standing by "
                        "until it is back.",
                    )
                self.sleep()
                continue
            self.answer_remote_facts(current)
            self.record_snapshot(current)
            if has_submit_control(self.page) and not submit_reported:
                console.print()
                console.print("  [bold]Submit found[/bold] — press it in the browser when ready.")
                submit_reported = True
            self.sleep()

    def capture_final_snapshot(self) -> None:
        """Best-effort final read before a clean terminal outcome."""
        try:
            fields = inspect_fields(self.page)
            if fields:
                self.record_snapshot(fields)
        except (Error, AttributeError):
            return

    def final_url(self) -> str:
        try:
            return self.page.url
        except Error:
            return ""
