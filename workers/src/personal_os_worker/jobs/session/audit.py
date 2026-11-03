"""The audit findings."""

from __future__ import annotations

import contextlib
from dataclasses import replace
from typing import Any

from playwright.sync_api import Error

from personal_os_worker.core.config import console
from personal_os_worker.jobs.answer_check import FilledAnswer
from personal_os_worker.jobs.audit import (
    REPAIRABLE_CAUSES,
    Finding,
    classify_empty,
    classify_unknown,
    order,
)
from personal_os_worker.jobs.autofill import (
    NOT_APPLICABLE,
    AnswerResolution,
    answer_above,
    build_autofill_plan,
    follow_up_condition,
    is_cover_letter_field,
    is_follow_up_box,
    is_long_form_field,
    is_sensitive_field,
    is_signature_field,
    offered_options,
    signature_resolution,
)
from personal_os_worker.jobs.browser import (
    audit_filled_form,
    fresh_control,
    inspect_fields,
    inspect_unknown_controls,
    snapshot_entries,
)
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.registry.formats import BLANK
from personal_os_worker.jobs.registry.strategies import CONTROL_TIMEOUT_MS
from personal_os_worker.jobs.registry.strategies.native import set_registered_value
from personal_os_worker.jobs.remote import gap_items
from personal_os_worker.jobs.repair.repairer import find_field
from personal_os_worker.jobs.replay import same_answer
from personal_os_worker.jobs.resolve import Miss, Resolver
from personal_os_worker.jobs.safety import armed
from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.context import (
    ASK_LIMIT,
    _question_for,
    echo,
)
from personal_os_worker.jobs.sites import failure_signature


class AuditMixin(SessionBase):
    # ----- the audit, and the repairs it drives ----------------------------

    def may_ask(self, question: str) -> bool:
        return self.state.asked_claude.get(question, 0) < ASK_LIMIT

    def note_asked(self, question: str) -> None:
        self.state.asked_claude[question] = self.state.asked_claude.get(question, 0) + 1

    def options_for(self, fields: list[dict[str, Any]], field: dict[str, Any]) -> list[str]:
        """The options the question offers, gathered the way the planner gathers them."""
        return offered_options(fields, field)

    def filled_answers(self, fields: list[dict[str, Any]]) -> list[FilledAnswer]:
        """Every short answer the page holds, with who put it there.

        Prose, cover letters, consent, signatures, and uploads are excluded: a
        fact cannot judge them.
        """
        try:
            entries = snapshot_entries(self.page, fields)
        except Error:
            return []
        by_question: dict[str, dict[str, Any]] = {}
        for item in fields:
            question = _question_for(item)
            if question:
                by_question.setdefault(question, item)
        answers: list[FilledAnswer] = []
        for entry in entries:
            question = str(entry["question"])
            value = str(entry["answer_text"]).strip()
            control = by_question.get(question)
            if not value or control is None or entry.get("form") == "long":
                continue
            if (
                is_sensitive_field(control)
                or is_long_form_field(control)
                or is_cover_letter_field(control)
                or (control.get("type") == "radio" and not control.get("group"))
            ):
                continue
            source = self.state.tier_source.get(question)
            if source is None:
                seed, seed_source = self.state.seeds.get(question, ("", "empty"))
                source = (
                    seed_source
                    if seed_source in {"prose", "model"} and value == seed.strip()
                    else "manual"
                )
            answers.append(
                FilledAnswer(
                    question, value, tuple(self.options_for(fields, control)), source
                )
            )
        return answers

    def wrong_answers(self, fields: list[dict[str, Any]]) -> list[Finding]:
        """Answers a fact says are wrong, judged once per answer per run."""
        if not self.ctx.use_claude:
            return []
        answers = self.filled_answers(fields)
        live = {answer.question: answer.answer for answer in answers}
        self.state.check_findings = {
            question: finding for question, finding in self.state.check_findings.items()
            if live.get(question) == finding.answer
        }
        fresh = [
            answer
            for answer in answers
            if answer.question not in self.state.judged
        ]
        if not fresh:
            return list(self.state.check_findings.values())
        self.state.judged.update(answer.question for answer in fresh)
        # A pattern answer equal to one you already submitted is known right:
        # judged, no finding, and no call spent on it.
        sightings = self.sightings()
        if sightings is not None:
            fresh = [
                answer
                for answer in fresh
                if answer.source != "pattern"
                or not any(
                    normalize_answer_question(sighting.question)
                    == normalize_answer_question(answer.question)
                    and same_answer(
                        answer.answer, sighting.answer, kind=sighting.kind
                    )
                    for sighting in sightings
                )
            ]
        if not fresh:
            return list(self.state.check_findings.values())
        console.print(f"  [dim]checking {len(fresh)} answer(s) against the facts[/dim]")
        judgements = self.call_model(self.ctx.checker,
            fresh,
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
        ) or {}
        held = {answer.question: answer for answer in fresh}
        required = {
            _question_for(item) for item in fields if item.get("required") is True
        }
        for unchecked in fresh:
            if unchecked.question not in judgements:
                self.state.check_findings[unchecked.question] = Finding(
                    unchecked.question, "model_error", unchecked.question in required,
                    self.state.model_error
                    or "the answer check returned no judgement",
                    answer=unchecked.answer, source=unchecked.source,
                )
        for judgement in judgements.values():
            answer = held.get(judgement.question)
            if judgement.kind != "wrong" or not judgement.fact_key or answer is None:
                continue
            self.state.check_findings[judgement.question] = Finding(
                judgement.question,
                "wrong_answer",
                required=judgement.question in required,
                note=judgement.note or f"{judgement.fact_key} says otherwise",
                fact_key=judgement.fact_key,
                answer=answer.answer,
                source=answer.source,
            )
        return list(self.state.check_findings.values())

    def pattern_pass(self, fields: list[dict[str, Any]], failures: list[dict[str, Any]]) -> bool:
        """Refill every open question a pattern or the engine answers, before any model call.

        A value a pattern filled that the page then lost (a re-render, a cleared
        widget) comes back from the same pattern here, never from a model. True
        when anything was filled.
        """
        try:
            current = inspect_fields(self.page)
            open_questions = audit_filled_form(current)
        except Error:
            return False
        wanted = {
            normalize_answer_question(question)
            for question in open_questions["required_unfilled"]
            + open_questions["optional_unfilled"]
            if question not in self.state.blanks
        }
        plan = build_autofill_plan(current, resolver=Resolver(self.ctx.knowledge))
        # Only what a pattern answers: an upload or a consent click the engine
        # made is never replayed, since some sites append a second attachment.
        planned = {
            key
            for fill in plan.fills
            if fill.kind == "text" and fill.chooser is None
            and fill.resolution is not None and fill.resolution.fact_key is not None
            and (key := normalize_answer_question(_question_for(current[fill.index]))) in wanted
        }
        if not planned:
            return False
        more = self.fill_deterministic(fields, only=planned, note="a pattern answers again")
        failures.extend(more.get("failures") or [])
        return bool(more.get("filled_fields"))

    def audit(
        self, fields: list[dict[str, Any]], failures: list[dict[str, Any]]
    ) -> list[Finding]:
        """What is still wrong with this page: nothing answered it, or a fact disagrees.

        A question a pattern answers but that is still empty is a control error:
        the value was right and did not stay, which is the repair lane's, never
        a model's.
        """
        try:
            current = inspect_fields(self.page)
            open_questions = audit_filled_form(current)
        except Error:
            return []
        by_question = {
            normalize_answer_question(str(failure.get("question") or failure.get("field_label")
                                          or "")): failure
            for failure in failures
        }
        resolved, misses = self._resolve_open(current, open_questions)
        findings = [
            classify_empty(
                question,
                required=required,
                needed=self.state.needed,
                misses=misses,
                failures=by_question,
                resolved=resolved,
            )
            for question, required in (
                [(item, True) for item in open_questions["required_unfilled"]]
                + [(item, False) for item in open_questions["optional_unfilled"]]
            )
            if question not in self.state.blanks
        ]
        # None remains a known fact even when this control cannot accept N/A.
        findings += [
            Finding(
                question,
                "fact_gap",
                question in open_questions["required_unfilled"],
                f"{fact} is none, but this control cannot represent N/A; answer still needed",
                fact_key=fact,
            )
            for question in (
                open_questions["required_unfilled"] + open_questions["optional_unfilled"]
            )
            if (fact := self.state.blanks.get(question)) is not None
        ]
        unidentified = {
            _question_for(item): item for item in current
            if item.get("type") == "radio" and not item.get("group")
        }
        findings = [item for item in findings if item.question not in unidentified]
        findings += [Finding(question, "detection_error", item.get("required") is True,
                             "radio options have no question label; extraction needs repair")
                     for question, item in unidentified.items()]
        findings = [
            replace(item, cause="draft_error", note=self.state.model_error
                    or "prose still needs an answer from approved writing or a grounded draft")
            if item.cause == "pattern_gap"
            and (control := find_field(current, item.question)) is not None
            and is_long_form_field(control) else item
            for item in findings
        ]
        captured = {normalize_answer_question(_question_for(item)) for item in current}
        with contextlib.suppress(Error):
            findings += classify_unknown(inspect_unknown_controls(self.page), captured)
        return order(findings + self.wrong_answers(current))

    def _resolve_open(
        self,
        current: list[dict[str, Any]],
        open_questions: dict[str, list[str]],
    ) -> tuple[dict[str, AnswerResolution], dict[str, Miss]]:
        """What a pattern or the engine answers among the open questions, and every miss."""
        resolver = Resolver(self.ctx.knowledge)
        resolved: dict[str, AnswerResolution] = {}
        for question in open_questions["required_unfilled"] + open_questions["optional_unfilled"]:
            field = find_field(current, question)
            if field is None or question in self.state.blanks:
                continue
            # A typed signature is signed with your legal name, as the planner signs it;
            # no other consent or attestation control is resolved here.
            if is_signature_field(field):
                resolution = signature_resolution(field, question, resolver)
            elif is_sensitive_field(field):
                continue
            else:
                resolution = resolver.resolve(
                    dict(field) | {"label": question, "options": self.options_for(current, field)}
                )
            if resolution is not None and resolution.formatter != BLANK:
                resolved[normalize_answer_question(question)] = resolution
        misses = {**self.state.misses, **resolver.misses}
        return resolved, misses

    def audit_and_repair(
        self, fields: list[dict[str, Any]], result: dict[str, Any]
    ) -> None:
        """Look, fix what has a path, look again, then report whatever is left.

        The repairs are silent: each one echoes the line it changed, and only
        what no lane could fix is listed for you at the end.
        """
        failures = list(result.get("failures") or [])
        self.reconcile_follow_ups(fields)
        self.pattern_pass(fields, failures)
        findings = self.audit(fields, failures)
        for _round in range(max(1, self.ctx.audit_rounds)):
            if not findings:
                break
            if not self.remediate(fields, findings):
                break
            self.pattern_pass(fields, failures)
            findings = self.audit(fields, failures)
        self.report_open(findings)
        self.persist_open(findings)
        if self.ctx.link is not None:
            self.ctx.link.gaps(gap_items(findings))
        self.escalate(findings)
        self.safe_point()

    def remediate(self, fields: list[dict[str, Any]], findings: list[Finding]) -> bool:
        """Run each finding's repair path; True when anything on the page changed."""
        acted = False
        prose = {item.question for item in findings if item.cause == "draft_error"}
        if prose:
            current = inspect_fields(self.page)
            self.fill_long_form([item for item in current if _question_for(item) in prose])
            acted = any(item.get("filled") is True and _question_for(item) in prose
                        for item in inspect_fields(self.page))
        if self.ctx.repair:
            acted = self.queue_repairs(fields, findings) or acted
        acted = self.ask_again(fields, findings) or acted
        for finding in findings:
            if finding.cause == "wrong_answer":
                acted = self.correct_answer(fields, finding) or acted
            elif finding.cause == "fact_gap" and self.ctx.ask_facts:
                acted = self.ask_for_fact(fields, finding) or acted
        # A repair above may have changed the answer a follow-up depends on.
        return self.reconcile_follow_ups(fields) or acted

    def reconcile_follow_ups(self, fields: list[dict[str, Any]]) -> bool:
        """Make every "If yes…" follow-up agree with the answer just above it, as the page holds it.

        An N/A the worker wrote is cleared once the answer above no longer rules
        the follow-up out (it was answered the other way, or the fill above never
        landed), so the follow-up is answered from a fact or left for you. An
        empty follow-up the answer above rules out gets N/A. An N/A you typed is
        yours. True when anything on the page changed.
        """
        try:
            current = inspect_fields(self.page)
        except Error:
            return False
        acted = False
        for item in current:
            question = _question_for(item)
            condition = follow_up_condition(question)
            if condition is None or not is_follow_up_box(item) or is_signature_field(item):
                continue
            above = answer_above(current, item)
            ruled_out = above is not None and above != condition
            try:
                value = str(fresh_control(self.page, item).input_value(
                    timeout=CONTROL_TIMEOUT_MS
                )).strip()
            except (Error, RuntimeError):
                continue
            ours = (
                self.state.tier_resolver.get(question) == NOT_APPLICABLE.resolver_id
                and value == NOT_APPLICABLE.value
            )
            if ours and not ruled_out:
                acted = self.clear_not_applicable(item, above=above) or acted
            elif not value and ruled_out:
                shown = self.apply_value(
                    fields, item, NOT_APPLICABLE.value,
                    source=NOT_APPLICABLE.source, resolver_id=NOT_APPLICABLE.resolver_id,
                )
                if shown is not None:
                    echo("source", question, shown, note="the answer above rules it out")
                    acted = True
        return acted

    def clear_not_applicable(self, field: dict[str, Any], *, above: bool | None) -> bool:
        """Take back an N/A the worker wrote into a follow-up that does apply."""
        question = _question_for(field)
        try:
            with armed(self.page):
                control = fresh_control(self.page, field)
                set_registered_value(control, "")
                cleared = not str(control.input_value(timeout=CONTROL_TIMEOUT_MS)).strip()
        except (Error, RuntimeError):
            cleared = False
        if not cleared:
            echo("failed", question, NOT_APPLICABLE.value, note="could not take back N/A")
            return False
        for answers in (self.state.tier_answers, self.state.tier_source,
                        self.state.tier_resolver, self.state.tier_fact):
            answers.pop(question, None)
        why = (
            f"the question above was answered {'Yes' if above else 'No'}, so it applies"
            if above is not None else "nothing above rules it out"
        )
        echo("repaired", question, note=f"{why}: N/A taken back")
        return True

    def queue_repairs(self, fields: list[dict[str, Any]], findings: list[Finding]) -> bool:
        """Record the audit's own failures, then let the repair lane retry them here.

        A question the live fill already retried is not retried again; what the
        audit adds is the field no fill event ever failed on, because no tier
        produced a value for it at all.
        """
        failures: list[dict[str, Any]] = []
        for finding in findings:
            if finding.cause not in REPAIRABLE_CAUSES:
                continue
            key = normalize_answer_question(finding.question)
            field = find_field(fields, finding.question)
            if field is None or key in self.state.repair_attempted:
                continue
            if not self.state.budget.allows(finding.question):
                continue
            category = (
                "format_miss" if finding.cause == "format_error" else "strategy_miss"
            )
            stage = "format" if finding.cause == "format_error" else "strategy"
            fingerprint = str(field.get("fingerprint") or finding.question)
            failures.append(
                {
                    "signature": failure_signature(self.ctx.ats, category, fingerprint),
                    "category": category,
                    "stopped_at": stage,
                    "question": finding.question,
                    "field_label": field.get("label") or finding.question,
                    "control_kind": field.get("type") or field.get("tag"),
                    "field_fingerprint": fingerprint,
                    "fact_key": finding.fact_key,
                    "formatter": finding.formatter,
                    "intended_value": finding.intended_value,
                    "error_message": finding.note or "the audit found this field empty",
                    "dom_signals": {
                        name: field.get(name)
                        for name in ("tag", "type", "name", "label", "group", "role")
                    },
                }
            )
        if not failures:
            return False
        issues = self.persist_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "failures": failures,
            },
        )
        self.remember_issues(failures, issues, lambda failure: (
            "format_error" if failure["category"] == "format_miss" else "control_error",
            failure["question"],
        ))
        self.repair_in_place(fields, failures, issues)
        return True
