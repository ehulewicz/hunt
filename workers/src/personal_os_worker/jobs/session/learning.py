"""Learning from the model."""

from __future__ import annotations

from typing import Any

import httpx
from playwright.sync_api import Error

from personal_os_worker.core.tables import console
from personal_os_worker.jobs.autofill import is_sensitive_field
from personal_os_worker.jobs.browser import (
    fresh_control,
    inspect_fields,
)
from personal_os_worker.jobs.learn import Lesson, binding_refusal, lesson_for
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.registry.strategies.combobox import combobox_options
from personal_os_worker.jobs.repair.repairer import find_field
from personal_os_worker.jobs.replay import (
    Replay,
    Sighting,
    replay_pattern,
    sightings_of,
)
from personal_os_worker.jobs.resolve import Resolver
from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.context import (
    _claude_questions,
    echo,
)
from personal_os_worker.jobs.short_answers import Verdict


class LearningMixin(SessionBase):
    # ----- learning from the model ---------------------------------------------

    def failure(self, question: str) -> dict[str, Any]:
        """The failure a change made in this run answers: the run, and its question."""
        key = normalize_answer_question(question)
        return {"run_id": self.ctx.run_id} | (
            {"question_key": key} if key and len(key) <= 1000 else {}
        )

    def bind(self, question: str, fact_key: str, *, origin: str) -> str | None:
        """Bind one question to a fact and add it to what this run knows; why not, if not."""
        return self.remember(
            {"kind": "binding", "question": question}, fact_key, origin=origin,
            failure=self.failure(question),
        )

    def remember(
        self,
        shape: dict[str, Any],
        fact_key: str,
        *,
        origin: str,
        status: str = "active",
        failure: dict[str, Any] | None = None,
        verification: dict[str, int] | None = None,
        use_for_run: bool = False,
    ) -> str | None:
        """Save a pattern, and add it to this run's knowledge once it is active; why not,
        if not. A binding replaces its question's binding here as it does in the API."""
        try:
            created = self.ctx.api.create_pattern(
                **shape, fact_key=fact_key, origin=origin, status=status, failure=failure,
                verification=verification,
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 409:
                # It would undo a decision of yours, or it already waits in the Inbox.
                return str(error.response.json().get("detail") or "the API refused it")
            return f"the pattern could not be saved (HTTP {error.response.status_code})"
        except (httpx.HTTPError, ReferenceError, RuntimeError) as error:
            return f"the pattern could not be saved ({error.__class__.__name__})"
        if created.get("status") == "active" or use_for_run:
            self.ctx.knowledge = self.ctx.knowledge.with_pattern(created | {"status": "active"})
        return None

    def sightings(self) -> list[Sighting] | None:
        """The answers you accepted, read once a run; None when they cannot be read."""
        if not self.state.sightings_read:
            self.state.sightings_read = True
            try:
                self.state.sightings = sightings_of(self.ctx.api.sightings())
            except (httpx.HTTPError, ReferenceError, RuntimeError):
                self.state.sightings = None
        return self.state.sightings

    def checked(self, lesson: Lesson) -> tuple[Lesson, Replay | None]:
        """Replay a lesson against the answers you accepted (R69).

        An expression that changes one for the worse falls back, once, to
        binding the question alone, replayed in turn. None when the answers
        could not be read: the lesson is saved as M2 saved it, unreplayed.
        """
        sightings = self.sightings()
        if sightings is None:
            return lesson, None
        replay = replay_pattern(sightings, self.ctx.knowledge, lesson.pattern())
        if replay.passed or lesson.kind == "binding" or binding_refusal(
            lesson.question, patterns=list(self.ctx.knowledge.patterns)
        ) is not None:
            return lesson, replay
        bound = lesson.bound(f"expression not kept: {replay.summary()}")
        return bound, replay_pattern(sightings, self.ctx.knowledge, bound.pattern())

    def save_lesson(
        self,
        question: str,
        fact_key: str,
        expression: str | None,
        questions: list[str] | None,
        target: dict[str, Any],
        *,
        overriding: bool = False,
    ) -> str | None:
        """Check what a verdict teaches and save it active; why nothing was saved, if not.

        The fact must be one this control can take (`target` is the field with
        its label and options), or the lesson would only ever fail at format.
        `questions` are the labels already met, which an expression must leave
        answered as they are. Without them no expression can be checked, so
        only the question itself is bound. `overriding` is for a question a
        pattern answered wrongly.
        """
        if Resolver(self.ctx.knowledge).render_fact(fact_key, target, question) is None:
            return f"{fact_key} fits none of this control's options"
        lesson, refusal = lesson_for(
            question,
            fact_key,
            expression if questions is not None else None,
            patterns=list(self.ctx.knowledge.patterns),
            questions=questions or [],
            overriding=overriding,
        )
        if lesson is None:
            return refusal
        if (question, fact_key) in self.state.held:
            return self.state.held[(question, fact_key)]
        lesson, replay = self.checked(lesson)
        held = replay is not None and not replay.passed
        failed = self.remember(
            lesson.shape(),
            fact_key,
            origin="model",
            status="candidate" if held else "active",
            failure=self.failure(question),
            verification=replay.verification() if replay is not None else None,
            use_for_run=held and overriding,
        )
        if lesson.kind == "expression" and failed == "this expression already waits in the Inbox":
            lesson, replay = self.checked(lesson.bound(f"expression not kept: {failed}"))
            held = replay is not None and not replay.passed
            failed = self.remember(
                lesson.shape(), fact_key, origin="model",
                status="candidate" if held else "active", failure=self.failure(question),
                verification=replay.verification() if replay is not None else None,
                use_for_run=held and overriding,
            )
        if failed is not None:
            return failed
        if held and replay is not None:
            reason = f"it waits in the Inbox as a pattern check: {replay.summary()}"
            if overriding:
                # The current answer was independently checked against a fact.
                # Repair this run, while the conflicting historical answer keeps
                # the reusable lesson pending the user's review.
                echo("review", question, note=f"correcting this run only; {reason}")
                return None
            self.state.held[(question, fact_key)] = reason
            return reason
        what = f"/{lesson.expression}/" if lesson.kind == "expression" else "bound"
        echo("learned", question, note=f"{what} -> {fact_key}"
             + (f"; {lesson.note}" if lesson.note else "")
             + (f"; {replay.summary()}" if replay is not None else ""))
        return None

    def learn(
        self, verdicts: dict[str, Verdict], targets: dict[str, dict[str, Any]]
    ) -> set[str]:
        """Save a checked pattern for every question a verdict says a fact answers.

        `targets` holds each asked question's field, labelled with the question
        and carrying the options it was asked with. Returns the questions a
        saved pattern now decides, for the pattern pass to fill. A dry run
        writes nothing, so it learns nothing.
        """
        fits = [
            verdict for verdict in verdicts.values()
            if verdict.kind == "fits_fact" and verdict.fact_key and verdict.question in targets
        ]
        if not fits or self.ctx.dry_run:
            return set()
        questions: list[str] | None
        try:
            questions = [str(item.get("sample_label") or "") for item in self.ctx.api.questions()]
        except (httpx.HTTPError, ReferenceError, RuntimeError):
            questions = None
        learned: set[str] = set()
        for verdict in fits:
            refusal = self.save_lesson(
                verdict.question, str(verdict.fact_key), verdict.expression, questions,
                targets[verdict.question],
            )
            if refusal is None:
                learned.add(verdict.question)
            else:
                echo("left", verdict.question, note=f"{verdict.fact_key}: {refusal}")
        return learned

    def refill_learned(
        self, fields: list[dict[str, Any]], learned: set[str], result: dict[str, Any]
    ) -> bool:
        """The pattern pass over the questions a lesson now decides; True when one filled."""
        if not learned or self.form_is_gone():
            return False
        more = self.fill_deterministic(
            fields,
            only={normalize_answer_question(question) for question in learned},
            note="answered by what was just learned",
        )
        for name in ("filled_fields", "skipped_fields", "failures"):
            result.setdefault(name, []).extend(more.get(name) or [])
        return bool(more.get("filled_fields"))

    def fill_with_claude(self, fields: list[dict[str, Any]], result: dict[str, Any]) -> None:
        """One bounded pass asking Claude which fact answers each question nothing decides.

        Claude only names a fact; what it teaches is saved as a pattern, and the
        pattern pass fills the field. A question an active pattern decides is
        never put to it, even when its fact is missing or fits no option.
        """
        current = inspect_fields(self.page)
        # A typeahead hides its choices until opened; Claude should pick from
        # them verbatim rather than guess at the wording.
        pending = [
            item
            for item in current
            if item.get("role") == "combobox"
            and item.get("filled") is not True
            and not is_sensitive_field(item)
        ]
        if pending:
            console.print(f"  [dim]reading the options of {len(pending)} dropdown(s)[/dim]")
        for item in pending:
            try:
                item["options"] = combobox_options(self.page, fresh_control(self.page, item))
            except (Error, RuntimeError):
                item["options"] = []
                if self.form_is_gone():
                    return
        # A question is put to the model at most ASK_LIMIT times in a run, so a
        # re-render that restarts the step does not ask again, while the audit
        # may ask a second time once a new fact has appeared.
        decides = Resolver(self.ctx.knowledge).decision
        questions = [
            question
            for question in _claude_questions(current)
            if self.may_ask(question.question)
            and question.question not in self.state.blanks
            and decides(question.question) is None
        ]
        if not questions:
            return
        for pending_question in questions:
            self.note_asked(pending_question.question)
        console.print(f"  [dim]asking Claude which fact answers {len(questions)} question(s)[/dim]")
        verdicts = self.call_model(self.ctx.short_drafter,
            questions,
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
            ranked=self.ranked_facts(questions),
            expressions=self.expressions_by_fact(),
        ) or {}
        for verdict in verdicts.values():
            if verdict.kind == "no_fact" and verdict.missing_fact:
                self.state.needed[verdict.question] = verdict.missing_fact
        if not any(item.kind == "fits_fact" for item in verdicts.values()):
            console.print("  [dim]Claude found no fact that answers them[/dim]")
            return
        if self.form_is_gone():
            return
        targets = {
            question.question: dict(field)
            | {"label": question.question, "options": list(question.options)}
            for question in questions
            if (field := find_field(current, question.question)) is not None
        }
        self.refill_learned(fields, self.learn(verdicts, targets), result)
