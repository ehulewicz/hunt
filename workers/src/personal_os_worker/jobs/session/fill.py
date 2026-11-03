"""The deterministic fill."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from playwright.sync_api import Error

from personal_os_worker.core.config import console
from personal_os_worker.jobs.autofill import (
    BLANK_KIND,
    NOT_APPLICABLE,
    AnswerResolution,
    AutofillPlan,
    FormFill,
    is_cover_letter_field,
    is_long_form_field,
)
from personal_os_worker.jobs.browser import (
    apply_autofill,
    inspect_fields,
    page_evidence,
    plan_autofill,
)
from personal_os_worker.jobs.explain import Explainer
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.registry.events import (
    planned_events,
    unresolved_events,
)
from personal_os_worker.jobs.registry.kinds import question_of
from personal_os_worker.jobs.repair.repairer import find_field, repair_format, repair_strategy
from personal_os_worker.jobs.resolve import Resolver
from personal_os_worker.jobs.safety import armed
from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.context import (
    REPAIR_COST,
    REVEAL_PASSES,
    _question_for,
    echo,
)
from personal_os_worker.jobs.sites import failure_signature


class FillMixin(SessionBase):
    # ----- deterministic fill ---------------------------------------------

    def explainer(self) -> Explainer | None:
        """The `--explain` trace for one pass; it pauses on each fill with `--step`."""
        if not (self.ctx.explain or self.ctx.step):
            return None
        if self._explainer is None:
            self._explainer = Explainer(self.page, step=self.ctx.step)
        # `c` while stepping stops the pauses for the rest of the run.
        self._explainer.page = self.page
        return self._explainer

    def fill_revealed(self, fields: list[dict[str, Any]], result: dict[str, Any]) -> None:
        """Fill the questions an answer revealed, such as race after Hispanic/Latino.

        A form can add controls once a choice is made. The page is read again
        after the pass, and anything empty that was not there before gets its
        own pass, at most `REVEAL_PASSES` times, so a form that keeps growing
        cannot hold the run.
        """
        seen = {normalize_answer_question(question_of(item)) for item in fields}
        for _ in range(REVEAL_PASSES):
            new = {
                key
                for item in inspect_fields(self.page)
                if item.get("filled") is not True
                and (key := normalize_answer_question(question_of(item)))
                and key not in seen
            }
            if not new:
                return
            seen |= new
            more = self.fill_deterministic(fields, only=new)
            for name in ("filled_fields", "skipped_fields", "failures"):
                result.setdefault(name, []).extend(more.get(name) or [])

    def fill_deterministic(
        self,
        fields: list[dict[str, Any]],
        *,
        only: set[str] | None = None,
        note: str = "appeared after answering",
    ) -> dict[str, Any]:
        """One deterministic pass; `only` limits it to these question keys, `note` says why."""
        resolver = Resolver(self.ctx.knowledge)
        fields[:], plan = plan_autofill(self.page, resolver=resolver)
        self.state.misses.update(resolver.misses)
        for item in fields:
            item["ats"] = self.ctx.ats

        def in_scope(item: dict[str, Any]) -> bool:
            return only is None or normalize_answer_question(question_of(item)) in only

        plan = self._narrow_plan(fields, plan, only, in_scope)
        explainer = self._announce_pass(fields, plan, only, note, resolver, in_scope)
        if self.ctx.dry_run:
            return self._persist_dry_run(fields, plan, explainer)
        before = page_evidence(self.page, fields, console_errors=self.ctx.console_errors)
        with armed(self.page):
            result = apply_autofill(
                self.page,
                fields,
                plan,
                resume_path=self.ctx.resume_pdf,
                transcript_path=self.ctx.transcript_pdf,
                assert_active=lambda: None,
                preferences=self.ctx.knowledge.preferences,
                observer=explainer,
            )
        if explainer is not None:
            explainer.finish()
        self._note_applied(fields, plan, result)
        self._unanswered_failures(fields, result)
        self._capture_evidence(fields, plan, result, before)
        issues = self._persist_pass(fields, plan, result, in_scope)
        if self.ctx.repair:
            self.repair_in_place(fields, result["failures"], issues)
        return result

    def _narrow_plan(
        self,
        fields: list[dict[str, Any]],
        plan: AutofillPlan,
        only: set[str] | None,
        in_scope: Callable[[dict[str, Any]], bool],
    ) -> AutofillPlan:
        """The plan this pass operates: the `only` questions, and no prose without a fact."""
        if only is not None:
            plan = type(plan)(
                tuple(fill for fill in plan.fills if in_scope(fields[fill.index])),
                tuple(
                    question for question in plan.unanswered_required_questions
                    if normalize_answer_question(question) in only
                ),
                plan.reasons,
            )
        if not self.ctx.dry_run:
            # Prose controls are seeded by fill_long_form, which knows their source,
            # unless a pattern names the fact a big box is asking for ("the school
            # you will attend", in a textarea): a fact beats a draft. So does the
            # N/A of a follow-up the answer above rules out.
            def from_a_fact(fill: FormFill) -> bool:
                return fill.resolution is not None and (
                    fill.resolution.fact_key is not None or fill.resolution == NOT_APPLICABLE
                )

            plan = type(plan)(
                tuple(
                    fill for fill in plan.fills
                    if not is_long_form_field(fields[fill.index]) or from_a_fact(fill)
                ),
                plan.unanswered_required_questions,
                plan.reasons | {
                    fill.index: "long-form: seeded from approved prose, else a Claude draft"
                    for fill in plan.fills
                    if is_long_form_field(fields[fill.index]) and not from_a_fact(fill)
                },
            )
        return plan

    def _announce_pass(
        self,
        fields: list[dict[str, Any]],
        plan: AutofillPlan,
        only: set[str] | None,
        note: str,
        resolver: Resolver,
        in_scope: Callable[[dict[str, Any]], bool],
    ) -> Explainer | None:
        """Print the pass's header and begin its `--explain` trace, when there is one."""
        if only is None:
            console.print()
            console.rule(f"[bold]Step {self.state.step + 1}[/bold]", align="left")
            console.print(
                f"  {len(fields)} controls · {len(plan.fills)} planned deterministically · "
                f"{len(plan.unanswered_required_questions)} required still open"
            )
        else:
            console.print(f"  [dim]{len(only)} question(s) {note}; "
                          f"{len(plan.fills)} planned[/dim]")
        explainer = self.explainer()
        if explainer is not None:
            explainer.begin(
                f"step {self.state.step + 1}, "
                + ("patterns and engine" if only is None else f"questions that {note}"),
                fields,
                plan,
                resolver,
                scope=in_scope,
            )
        return explainer

    def _persist_dry_run(
        self,
        fields: list[dict[str, Any]],
        plan: AutofillPlan,
        explainer: Explainer | None,
    ) -> dict[str, Any]:
        """Record a dry run's planned events and return its empty result."""
        if explainer is not None:
            explainer.dry_run()
        self.persist_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "fill_events": planned_events(fields, plan, misses=self.state.misses),
            },
        )
        return {"filled_fields": [], "skipped_fields": [], "failures": [], "plan": plan}

    def _note_applied(
        self,
        fields: list[dict[str, Any]],
        plan: AutofillPlan,
        result: dict[str, Any],
    ) -> None:
        """Remember what each fill applied and where it came from, and echo it."""
        for fill in plan.fills:
            question = _question_for(fields[fill.index])
            applied = result["applied"].get(fill.index)
            if fill.kind == BLANK_KIND and question and fill.resolution is not None:
                fact = str(fill.resolution.fact_key or "")
                self.state.blanks[question] = fact
                echo("blank", question, note=f"{fact} is none")
            if applied is not None and question:
                self.state.planned[question] = applied
                if fill.resolution is not None:
                    field = fields[fill.index]
                    key = f"{self.state.step}:{field.get('fingerprint') or ''}#{fill.index}"
                    self.state.resolutions[key] = AnswerResolution(
                        applied, fill.resolution.source, fill.resolution.resolver_id
                    )
                if fill.chooser is not None:
                    echo(fill.chooser, question, applied)
                elif fill.resolution is not None:
                    self.state.tier_answers[question] = applied
                    self.state.tier_source[question] = fill.resolution.source
                    self.state.tier_resolver[question] = fill.resolution.resolver_id
                    if fill.resolution.fact_key:
                        self.state.tier_fact[question] = fill.resolution.fact_key
                    if fill.resolution.resolver_id.startswith("engine:"):
                        echo("source", question, applied)
                    else:
                        echo(fill.resolution.source, question, applied,
                             note=fill.resolution.fact_key)

    def _unanswered_failures(
        self,
        fields: list[dict[str, Any]],
        result: dict[str, Any],
    ) -> None:
        """Add a required_field_empty failure for every required question still open."""
        for question in result["unanswered_required_questions"]:
            item = next(
                (
                    candidate
                    for candidate in fields
                    if str(candidate.get("label") or candidate.get("group") or "") == question
                ),
                {},
            )
            fingerprint = str(item.get("fingerprint") or question)
            result["failures"].append(
                {
                    "signature": failure_signature(
                        self.ctx.ats, "required_field_empty", fingerprint
                    ),
                    "category": "required_field_empty",
                    "field_label": question,
                    "control_kind": item.get("type") or item.get("tag"),
                    "field_fingerprint": fingerprint,
                    "error_message": "Required field remained empty after deterministic planning",
                    "dom_signals": {
                        key: item.get(key)
                        for key in ("tag", "type", "name", "label", "group", "role")
                    },
                }
            )

    def _capture_evidence(
        self,
        fields: list[dict[str, Any]],
        plan: AutofillPlan,
        result: dict[str, Any],
        before: dict[str, bytes],
    ) -> None:
        """Keep the page's evidence from before and after the pass, plan and verification."""
        after = page_evidence(
            self.page, inspect_fields(self.page), console_errors=self.ctx.console_errors
        )
        after["fill-plan.json"] = json.dumps(
            [
                {
                    "field": fields[fill.index].get("fingerprint"),
                    "kind": fill.kind,
                    "has_value": fill.value is not None,
                }
                for fill in plan.fills
            ],
            indent=2,
        ).encode()
        after["verification.json"] = json.dumps(
            {key: result[key] for key in ("filled_fields", "skipped_fields", "failures")}
            | {"unanswered_required_questions": result["unanswered_required_questions"]}
            | {"submitted": False},
            indent=2,
            default=str,
        ).encode()
        self.evidence = {**before, **{f"after-{k}": v for k, v in after.items()}}

    def _persist_pass(
        self,
        fields: list[dict[str, Any]],
        plan: AutofillPlan,
        result: dict[str, Any],
        in_scope: Callable[[dict[str, Any]], bool],
    ) -> list[dict[str, Any]]:
        """Upload evidence when a fill went wrong, persist the step, and return its issues."""
        artifact_paths: dict[str, str] = {}
        # A field left for the user is expected; only a fill that went wrong
        # earns the page evidence, which is twenty uploads.
        if any(failure["category"] != "required_field_empty" for failure in result["failures"]):
            artifact_paths = self.upload_evidence(self.evidence)
            for failure in result["failures"]:
                failure["artifact_paths"] = artifact_paths
        issues = self.persist_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "filled_count": len(result["filled_fields"]),
                "skipped_count": len(result["skipped_fields"]),
                "failures": result["failures"],
                # Prose and cover letter controls are filled by their own paths.
                "fill_events": result["events"] + unresolved_events(
                    fields,
                    plan,
                    skip=lambda item: is_long_form_field(item)
                    or is_cover_letter_field(item)
                    or not in_scope(item),
                    misses=self.state.misses,
                ),
            },
        )
        result["artifact_paths"] = artifact_paths
        return issues

    def repair_in_place(
        self,
        fields: list[dict[str, Any]],
        failures: list[dict[str, Any]],
        issues: list[dict[str, Any]],
    ) -> None:
        """Retry each field that stopped at a format or a strategy, on this page.

        Any site works the same way, because the retry is about the control, not
        the board. What reads back fills the field now and is stored on its
        failure as a repaired candidate; `os hunt validate` decides whether it
        becomes the rule. Never touches a control that reads as submitting.
        """
        by_signature = {str(issue.get("signature")): issue for issue in issues}
        for failure in failures:
            stage = failure.get("stopped_at")
            issue = by_signature.get(str(failure.get("signature")))
            if stage not in {"format", "strategy"} or issue is None:
                continue
            question = str(failure.get("question") or failure.get("field_label") or "")
            field = find_field(fields, question)
            if field is None:
                continue
            # The audit must not re-queue what has already been retried here.
            self.state.repair_attempted.add(normalize_answer_question(question))
            self.state.budget.charge(question, REPAIR_COST)
            try:
                with armed(self.page):
                    if stage == "format" and failure.get("fact_key"):
                        candidate = repair_format(
                            self.page, field, self.ctx.knowledge, str(failure["fact_key"]),
                            failed_formatter=failure.get("formatter"),
                        )
                    elif stage == "strategy" and failure.get("intended_value"):
                        candidate = repair_strategy(
                            self.page, field, str(failure["intended_value"])
                        )
                    else:
                        candidate = None
            except (Error, RuntimeError):
                candidate = None
            if candidate is None:
                echo("failed", question, note=f"{stage}: nothing else took; left for you")
                continue
            try:
                self.ctx.api.update_autofill_failure(
                    str(issue["id"]), {"status": "repaired", "candidate": candidate}
                )
            except httpx.HTTPError as error:
                # The repaired value stays on the page; only its record is lost.
                self.warn_once(
                    f"repair-record|{issue['id']}",
                    f"Could not record the repair of {question!r} "
                    f"({error.__class__.__name__}); the value stays on the page.",
                )
            rule = candidate.get("strategy") if stage == "strategy" else candidate.get("formatter")
            echo("repaired", question, str(candidate.get("value") or ""), note=f"{stage}: {rule}")
