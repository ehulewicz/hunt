"""The repairs the audit drives."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx
from playwright.sync_api import Error
from rich.markup import escape

from personal_os_worker.core.config import console
from personal_os_worker.jobs.answer_check import WORKER_SOURCES
from personal_os_worker.jobs.audit import (
    Finding,
    summarize,
)
from personal_os_worker.jobs.autofill import (
    AnswerResolution,
    is_sensitive_field,
    offered_options,
)
from personal_os_worker.jobs.browser import (
    highlight_unfilled,
    inspect_fields,
    page_evidence,
    visible_actions,
)
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.registry.events import fill_event
from personal_os_worker.jobs.remote import FACT_KEY, gap_items
from personal_os_worker.jobs.repair.repairer import find_field, repair_strategy
from personal_os_worker.jobs.replay import replay_fact
from personal_os_worker.jobs.resolve import Knowledge, Resolver
from personal_os_worker.jobs.safety import armed
from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.context import (
    PERSISTED_CAUSES,
    REPAIR_COST,
    _question_for,
    echo,
    member_control,
)
from personal_os_worker.jobs.short_answers import ShortQuestion
from personal_os_worker.jobs.sites import failure_signature


class RepairsMixin(SessionBase):
    def ask_again(self, fields: list[dict[str, Any]], findings: list[Finding]) -> bool:
        """Put every question nothing decides back to the model, once more, with the options.

        A fact gap a pattern names is not the model's: the pattern is right and
        only its fact is missing. What the model teaches is saved as a pattern,
        and the pattern pass fills the field.
        """
        if not self.ctx.use_claude:
            return False
        questions: list[ShortQuestion] = []
        targets: dict[str, dict[str, Any]] = {}
        for finding in findings:
            if finding.cause not in {"pattern_gap", "fact_gap"} or finding.fact_key:
                continue
            field = find_field(fields, finding.question)
            if (
                field is None
                or not self.may_ask(finding.question)
                or finding.question in self.state.blanks
                or is_sensitive_field(field)
            ):
                continue
            targets[finding.question] = field
            questions.append(
                ShortQuestion(
                    finding.question, tuple(self.options_for(fields, field))
                )
            )
        if not questions:
            return False
        for pending_question in questions:
            self.note_asked(pending_question.question)
        console.print(
            f"  [dim]asking Claude again about {len(questions)} unanswered question(s)[/dim]"
        )
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
        learned = self.learn(verdicts, {
            name: dict(field) | {"label": name, "options": self.options_for(fields, field)}
            for name, field in targets.items()
        })
        return self.refill_learned(fields, learned, {})

    def correct_answer(self, fields: list[dict[str, Any]], finding: Finding) -> bool:
        """Bind a question the worker answered wrongly to the fact that contradicts it, and
        refill it through the pattern path.

        The model only named the fact. An answer you typed, one the engine
        chose, and one a binding you accepted produced are reported instead. On a
        `--submit` run nobody is typing, so an answer the worker did not write is
        one the site preselected, and it is corrected like the worker's own.
        """
        key = str(finding.fact_key or "")
        if key not in self.ctx.knowledge.facts:
            return False
        engine = self.state.tier_resolver.get(finding.question, "").startswith("engine:")
        if (finding.source not in WORKER_SOURCES and not self.ctx.submit) or engine:
            echo(
                "review",
                finding.question,
                finding.answer,
                note=f"{key} says otherwise ({finding.note}); yours to change",
            )
            return False
        field = find_field(fields, finding.question)
        if field is None:
            return False
        target = dict(field) | {
            "label": finding.question, "options": self.options_for(fields, field)
        }
        rendering = Resolver(self.ctx.knowledge).render_fact(key, target, finding.question)
        if rendering is None or rendering.value == finding.answer:
            return False
        refusal = self.save_lesson(
            finding.question, key, None, None, target, overriding=True
        )
        if refusal is not None:
            echo("review", finding.question, finding.answer,
                 note=f"{key} says otherwise ({finding.note}); {refusal}")
            return False
        resolution = Resolver(self.ctx.knowledge).resolve(target)
        if resolution is None or resolution.value == finding.answer:
            return False
        shown = self.apply_value(
            fields,
            field,
            resolution.value,
            source="pattern",
            resolver_id=resolution.resolver_id,
            fact_key=resolution.fact_key,
            formatter=resolution.formatter,
            pattern_id=resolution.pattern_id,
        )
        if shown is None:
            return False
        echo("repaired", finding.question, shown, note=f"{key} said {finding.note or 'otherwise'}")
        return True

    def ask_for_fact(self, fields: list[dict[str, Any]], finding: Finding) -> bool:
        """Ask at the terminal for the fact this question needs, then fill it in."""
        from personal_os_worker.jobs.facts.catalog import entry
        from personal_os_worker.jobs.facts.commands import VALUE_TYPES
        from personal_os_worker.jobs.facts.derived import read_only_refusal

        key = str(finding.fact_key or "")
        console.print()
        console.print(f"  [bold]{escape(finding.question)}[/bold]")
        if not key:
            console.print(f"  needs a fact: {finding.missing_fact}", style="dim")
            key = self.ctx.prompt("  fact key (blank to skip): ").strip()
            if not key:
                return False
        refusal = read_only_refusal(key)
        if refusal is not None:
            console.print(f"  {refusal}", style="dim")
            return False
        known = entry(key)
        label = known.label if known else self.ctx.prompt("  label: ").strip()
        value_type = (
            known.value_type
            if known
            else self.ctx.prompt(f"  type ({', '.join(VALUE_TYPES)}): ").strip()
        )
        if not label or value_type not in VALUE_TYPES:
            console.print("  a fact needs a label and a type; skipped", style="yellow")
            return False
        raw = self.ctx.prompt(f"  value ({value_type}), blank to skip: ").strip()
        if not raw:
            return False
        ok, message = self.set_fact_and_fill(
            fields, finding.question, key, label=label, value_type=str(value_type), raw=raw
        )
        if not ok:
            console.print(f"  {message}", style="red")
        return ok

    def answer_remote_facts(self, fields: list[dict[str, Any]]) -> None:
        """Facts the Hunt page sent for this step's gaps: set each, then fill its question."""
        link = self.ctx.link
        if link is None:
            return
        for answer in link.fact_answers():
            question = str(answer.get("question") or "")
            key = str(answer.get("key") or "").strip()
            ok, message = (
                self.set_fact_and_fill(
                    fields,
                    question,
                    key,
                    label=str(answer.get("label") or "").strip(),
                    value_type=str(answer.get("value_type") or "").strip(),
                    raw=str(answer.get("value") or ""),
                )
                if key
                else (False, "name the fact this question needs")
            )
            if not ok:
                echo("left", question, note=message)
            link.fact_result(question, ok=ok, message=message)

    def set_fact_and_fill(
        self,
        fields: list[dict[str, Any]],
        question: str,
        key: str,
        *,
        label: str,
        value_type: str,
        raw: str,
    ) -> tuple[bool, str]:
        """Store the fact a question needs, then fill the question from it.

        The terminal's --ask-facts and the Hunt page both end here; they differ
        only in where the key, label, type, and value come from. True once the
        fact is stored, with what was filled; False with the reason it was not.
        """
        from personal_os_worker.jobs.facts.catalog import entry
        from personal_os_worker.jobs.facts.commands import VALUE_TYPES, parse_value
        from personal_os_worker.jobs.facts.derived import read_only_refusal

        if not FACT_KEY.fullmatch(key):
            return False, f"{key!r} is not a fact key; use dotted lowercase words"
        refusal = read_only_refusal(key)
        if refusal is not None:
            return False, refusal
        known = entry(key)
        label = known.label if known else label
        value_type = known.value_type if known else value_type
        if not label or value_type not in VALUE_TYPES:
            return False, "a fact needs a label and a type"
        try:
            value = parse_value(value_type, raw)
        except ValueError as error:
            return False, str(error)
        sightings = self.sightings()
        check = (
            replay_fact(sightings, self.ctx.knowledge, key, value, value_type=value_type)
            if sightings is not None else None
        )
        gap = self.state.gap_issues.get(question)
        try:
            self.ctx.api.set_fact(
                key, value, label=label, value_type=value_type,
                failure=self.failure(question) | ({"issue_id": gap} if gap else {}),
                verification=check.verification() if check is not None else None,
            )
        except httpx.HTTPStatusError as error:
            return False, f"the API refused {key} (HTTP {error.response.status_code})"
        self.refresh_knowledge()
        echo("fact", question, str(value), note=f"set {key}")
        if check is not None and not check.passed:
            # Yours to keep: what disagrees waits in the Inbox beside the fact.
            echo("review", question, str(value), note=f"{key}: {check.summary()}")
        field = find_field(fields, question)
        if field is None:
            return True, f"set {key}"
        decided = Resolver(self.ctx.knowledge).decision(question)
        if decided is None or str(decided["fact_key"]) != key:
            # You named the fact this question needs: bind it, as the Inbox would.
            refusal = self.bind(question, key, origin="inbox")
            if refusal is not None:
                return True, f"set {key}; {refusal}"
        resolution = Resolver(self.ctx.knowledge).resolve(
            dict(field) | {"label": question, "options": self.options_for(fields, field)}
        )
        if resolution is None:
            return True, f"set {key}"
        shown = self.apply_value(
            fields,
            field,
            resolution.value,
            source="pattern",
            resolver_id=resolution.resolver_id,
            fact_key=resolution.fact_key,
            formatter=resolution.formatter,
            pattern_id=resolution.pattern_id,
        )
        if shown is not None:
            echo("pattern", question, shown, note=key)
        return True, shown or f"set {key}"

    def refresh_knowledge(self) -> None:
        """Rebuild what the run knows after a fact was set, keeping its preferences."""
        from personal_os_worker.jobs.facts.derived import POSTING_FACTS

        old = self.ctx.knowledge
        rebuilt = Knowledge.build(
            facts=self.ctx.api.facts(),
            career=self.ctx.career,
            patterns=old.patterns,
            prose=old.prose,
            company=self.ctx.company,
            seed=old.seed,
        )
        # Posting facts (its term and window, a referral, your history with the
        # company) came from this run's inputs, not from your facts: keep them.
        kept = {item.key for item in POSTING_FACTS if item.key in old.facts}
        self.ctx.knowledge = replace(
            rebuilt,
            facts={**rebuilt.facts, **{key: old.facts[key] for key in kept}},
            types={**rebuilt.types, **{key: old.types[key] for key in kept if key in old.types}},
            preferences=old.preferences,
        )

    def apply_value(
        self,
        fields: list[dict[str, Any]],
        field: dict[str, Any],
        value: str,
        *,
        source: str,
        resolver_id: str,
        fact_key: str | None = None,
        formatter: str | None = None,
        pattern_id: str | None = None,
    ) -> str | None:
        """Set the control this value answers, read it back, and record the attempt.

        Every strategy the control kind supports is tried, so this is also how a
        repair reaches a widget the first pass could not drive.
        """
        question = _question_for(field)
        spent = self.state.budget.exhausted(question)
        if spent is not None:
            echo("left", question, note=spent)
            return None
        target = member_control(fields, field, value)
        if target is None:
            echo("left", question, note=f"no option answers {value!r}")
            return None
        field = target
        self.state.budget.charge(question, REPAIR_COST)
        try:
            with armed(self.page):
                candidate = repair_strategy(self.page, field, value)
        except (Error, RuntimeError):
            candidate = None
        shown = str(candidate.get("value") or "") if candidate is not None else ""
        resolution = AnswerResolution(
            shown or value, source, resolver_id, fact_key=fact_key, formatter=formatter,
            pattern_id=pattern_id,
        )
        self.persist_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "fill_events": [
                    fill_event(
                        field,
                        strategy=str(candidate.get("strategy")) if candidate else None,
                        stopped_at=None if candidate else "strategy",
                        operated_ok=candidate is not None,
                        value=shown or value,
                        resolution=resolution,
                        options=offered_options(fields, field),
                    )
                ],
            },
        )
        if candidate is None:
            return None
        key = f"{self.state.step}:{field.get('fingerprint') or ''}#{field.get('index')}"
        self.state.resolutions[key] = resolution
        self.state.announced[key] = shown
        self.state.tier_answers[question] = shown
        self.state.tier_source[question] = source
        self.state.tier_resolver[question] = resolver_id
        if fact_key:
            self.state.tier_fact[question] = fact_key
        else:
            self.state.tier_fact.pop(question, None)
        return shown

    def report_open(self, findings: list[Finding]) -> None:
        """What the audit could not fix, required first, outlined on the page.

        Optional fields count: nothing is left blank unless you leave it. Each
        line says the cause, so what you do about it is never a guess.
        """
        self.state.outlined = {item.question for item in findings}
        with contextlib.suppress(Error):
            highlight_unfilled(self.page, inspect_fields(self.page), self.state.outlined)
        if not findings:
            console.print("  [dim]every field is filled, and no fact disagrees[/dim]")
            return
        empty = [item for item in findings if item.cause not in {"wrong_answer", "model_error"}]
        wrong = [item for item in findings if item.cause == "wrong_answer"]
        unchecked = [item for item in findings if item.cause == "model_error"]
        required = sum(1 for item in empty if item.required)
        console.print()
        if empty:
            console.print(
                f"  [bold]Left for you[/bold] ({required} required, "
                f"{len(empty) - required} optional, outlined on the page): "
                f"[dim]{summarize(empty)}[/dim]"
            )
            for item in empty:
                echo(
                    "left" if item.required else "optional",
                    item.question,
                    note=f"{item.cause.replace('_', ' ')}: {item.note}" if item.note
                    else item.cause.replace("_", " "),
                )
        if wrong:
            console.print()
            console.print("  [bold]A fact disagrees with these answers[/bold]:")
            for item in wrong:
                echo("review", item.question, item.answer, note=f"{item.fact_key}: {item.note}")
        if unchecked:
            console.print("  [bold]Answer audit incomplete[/bold]:")
            for item in unchecked:
                echo("review", item.question, item.answer, note=item.note)

    def persist_open(self, findings: list[Finding]) -> None:
        """Store what the audit left that only you or new code can settle, once a run.

        A missing fact, a widget no kind operates, and a question that could not
        be read become failure issues, so the Inbox and the fixer find them after
        the run as well as during it. Format and control errors are stored by
        `queue_repairs`, and walls by `report_wall`.
        """
        failures: list[dict[str, Any]] = []
        needs_evidence = False
        for finding in findings:
            if finding.cause not in PERSISTED_CAUSES:
                continue
            key = (finding.cause, normalize_answer_question(finding.question))
            if key in self.state.persisted_gaps:
                continue
            self.state.persisted_gaps.add(key)
            evidence = dict(finding.evidence or {})
            fingerprint = str(evidence.get("fingerprint") or finding.question)
            needs_evidence = needs_evidence or finding.cause != "fact_gap"
            failures.append(
                {
                    "signature": failure_signature(self.ctx.ats, finding.cause, fingerprint),
                    "category": finding.cause,
                    "question": finding.question,
                    "field_label": finding.question,
                    "control_kind": evidence.get("role"),
                    "field_fingerprint": fingerprint,
                    "fact_key": finding.fact_key,
                    "error_message": finding.note,
                    "dom_signals": {
                        name: value for name, value in (
                            evidence | {"missing_fact": finding.missing_fact,
                                        "required": finding.required}
                        ).items()
                        if name != "html" and value is not None
                    },
                }
            )
        if not failures:
            return
        if needs_evidence:
            with contextlib.suppress(Error):
                paths = self.upload_evidence(
                    page_evidence(self.page, inspect_fields(self.page),
                                  console_errors=self.ctx.console_errors)
                )
                for failure in failures:
                    if failure["category"] != "fact_gap":
                        failure["artifact_paths"] = paths
        with contextlib.suppress(httpx.HTTPError):
            issues = self.persist_step(
                self.ctx.run_id,
                {"step_number": self.state.step, "url": self.page.url, "failures": failures},
            )
            self.remember_issues(failures, issues,
                                 lambda failure: (failure["category"], failure["question"]))

    def remember_issues(
        self,
        failures: list[dict[str, Any]],
        issues: list[dict[str, Any]],
        problem: Callable[[dict[str, Any]], tuple[str, str]],
    ) -> None:
        """Note which failure issue each stored failure became, by its problem's key."""
        by_signature = {str(issue.get("signature")): str(issue.get("id")) for issue in issues}
        for failure in failures:
            issue_id = by_signature.get(str(failure.get("signature")))
            if issue_id:
                self.state.issue_ids[self.problem_key(*problem(failure))] = issue_id
                if failure.get("category") == "fact_gap":
                    self.state.gap_issues[str(failure.get("question"))] = issue_id

    def report_wall(self, category: str, message: str) -> Finding:
        """A wall between the run and its form is a finding, like an empty field.

        It is stored with the page as evidence and what the page offered, and
        goes to the page's gaps, so the fixer and you both see what blocked it.
        """
        offered: list[str] = []
        with contextlib.suppress(Error):
            offered = visible_actions(self.page)
        finding = Finding(
            "Reach the application form",
            "wall",
            True,
            message,
            evidence={"category": category, "url": self.page.url, "step": self.state.step,
                      "offered": offered},
        )
        self.state.walls.append(finding)
        issues = self.report_page_failure(category, message, offered=offered)
        if issues:
            self.state.issue_ids[self.problem_key("wall", finding.question)] = str(
                issues[0].get("id"))
        if self.ctx.link is not None:
            self.ctx.link.gaps(gap_items([finding]))
        # Signing in is yours and the account flow's; never a code fix.
        if category != "account_wall":
            self.escalate([finding])
        return finding
