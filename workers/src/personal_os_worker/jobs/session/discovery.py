"""Form discovery, the account wall, and the gate between posting and form."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import Error

from personal_os_worker.core.config import console
from personal_os_worker.jobs.account import (
    GOOGLE_HOST,
    AccountOutcome,
    AccountResult,
)
from personal_os_worker.jobs.autofill import (
    AnswerResolution,
    is_resume_field,
)
from personal_os_worker.jobs.browser import (
    inspect_fields,
    registrable_site,
    reveal_application_form,
)
from personal_os_worker.jobs.gateway import (
    click_gateway,
    form_appeared,
    inspect_gateways,
    observed_choice,
)
from personal_os_worker.jobs.options import normalize_answer_question
from personal_os_worker.jobs.page_state import (
    ApplicationPageState,
    capture_tab_navigation,
    classify_application_page,
    normalize_navigation_url,
)
from personal_os_worker.jobs.registry.events import fill_event
from personal_os_worker.jobs.resolve import Resolver
from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.context import echo
from personal_os_worker.jobs.short_answers import ShortQuestion


class DiscoveryMixin(SessionBase):
    # ----- form discovery --------------------------------------------------

    def wait_for_form(self) -> list[dict[str, Any]] | None:
        """Return the form's fields, or None once the page shows the application went in."""
        wall_reported = False
        unsettled_since: float | None = None
        while True:
            self.page = self.ctx.browser.active_page(self.page)
            fields = inspect_fields(self.page)
            if self.state.navigation is None:
                self.state.navigation = capture_tab_navigation(self.page)
            page_state = classify_application_page(self.page, fields, self.state.navigation)
            if page_state == ApplicationPageState.SUBMITTED:
                return None
            if page_state == ApplicationPageState.FORM:
                self.note_observed_gate()
                self.state.form_seen = True
                self.state.form_site = self.state.form_site or registrable_site(self.page.url)
                for item in fields:
                    item["ats"] = self.ctx.ats
                return fields
            if page_state == ApplicationPageState.OVERVIEW and self.state.form_seen:
                self.warn_once(
                    "overview-standby",
                    "Ashby Overview is open; standing by until you return to Application.",
                )
                self.sleep()
                self.stalled()
                continue
            if not self.state.form_seen:
                reveal_application_form(self.page)
                refreshed = inspect_fields(self.page)
                # The click may have changed the page: a sign-in it opened is no wall.
                page_state = classify_application_page(
                    self.page, refreshed, self.state.navigation
                )
                if page_state == ApplicationPageState.FORM:
                    self.state.form_seen = True
                    for item in refreshed:
                        item["ats"] = self.ctx.ats
                    return refreshed
            if page_state == ApplicationPageState.LOADING:
                unsettled_since = unsettled_since or time.monotonic()
                if time.monotonic() - unsettled_since < 5:
                    self.sleep()
                    continue
            if page_state == ApplicationPageState.ACCOUNT:
                # The account step is the gate in front of the gate.
                result = self.through_the_account_wall()
                if result is not None and result.outcome == AccountOutcome.SIGNED_IN:
                    continue
                wall_reported = wall_reported or result is not None
            elif self.pass_the_gate():
                continue
            if not wall_reported and self._learned_form_route():
                continue
            if not wall_reported:
                self.report_wall(
                    "navigation_wall",
                    "No unambiguous Apply control, gate, or account page led to a form",
                )
                wall_reported = True
                console.print(
                    "[yellow]Hit a wall:[/yellow] I could not safely reach the application "
                    "form. Use the open browser to reach it; I am watching the page."
                )
            self.sleep()
            self.stalled()

    def _learned_form_route(self) -> bool:
        """Whether the site's learned route reached a form; tried before a wall."""
        from personal_os_worker.jobs import registry
        from personal_os_worker.jobs.registry.api import SiteHandle

        for route in registry.learned("routes", registrable_site(self.page.url)):
            if route.goal != "form":
                continue
            try:
                if route.run(SiteHandle(self.page), "", ""):
                    fields = inspect_fields(self.page)
                    reached = (
                        classify_application_page(
                            self.page, fields, self.state.navigation
                        )
                        == ApplicationPageState.FORM
                    )
                    registry.report(route, reached)
                    if reached:
                        return True
            except (Error, RuntimeError):
                continue
        return False

    # ----- the account wall --------------------------------------------------

    def through_the_account_wall(self) -> AccountResult | None:
        """Sign in past an account page, once per page; None when it was not tried.

        A wall the flow could not pass is reported once and then left to you:
        the flow never runs again in this session, so it cannot type over what
        you are doing in the browser.
        """
        key = normalize_navigation_url(self.page.url)
        if self.ctx.dry_run or self.state.account_parked or key in self.state.account_tried:
            return None
        # Google's own sign-in (a popup left open, a late redirect) is never an ATS wall.
        if urlsplit(self.page.url).hostname == GOOGLE_HOST:
            return None
        self.state.account_tried.add(key)
        if self.ctx.account is None:
            console.print(
                "  [dim]account page: set the contact.email fact and I sign in for you; "
                "for now it is yours[/dim]"
            )
            return None
        console.print(f"  account page: signing in as {self.ctx.account.email}")
        result = self.ctx.account.run(self.page)
        if result.outcome == AccountOutcome.SIGNED_IN:
            console.print(f"  [green]{result.reason}[/green]")
            return result
        self.state.account_parked = True
        console.print(
            f"[yellow]Account wall:[/yellow] {result.reason}. "
            "Finish it in the open browser; I am watching the page."
        )
        self.report_wall("account_wall", result.reason)
        return result

    # ----- the gate between the posting and its form ------------------------

    def pass_the_gate(self) -> bool:
        """Answer a gate the way a fact says, and report whether the form opened.

        A gate is a choice like any other: the pattern you accepted answers it,
        else the model names which fact does. Nothing is clicked without a fact
        behind it, and the readback is the form itself appearing (design §10.2).
        """
        gates = inspect_gateways(self.page)
        if not gates:
            return False
        gate = gates[0]
        question = str(gate["label"])
        self.state.gate_pending = gate
        if question in self.state.gates_tried:
            return False
        self.state.gates_tried.add(question)
        resolver = Resolver(self.ctx.knowledge)
        resolution = resolver.resolve(gate)
        # A gate a pattern decides is never the model's, even when its fact is missing.
        if resolution is None and self.ctx.use_claude and resolver.decision(question) is None:
            resolution = self.gate_from_claude(gate)
        if resolution is None:
            console.print(
                f"  [dim]a way in is being asked for ({', '.join(gate['options'])}); "
                "no fact answers it yet, so it is yours to click[/dim]"
            )
            return False
        clicked = click_gateway(self.page, gate, resolution.value)
        opened = form_appeared(self.page) if clicked else False
        self.record_gate(gate, resolution=resolution, clicked=clicked, opened=opened)
        if opened:
            echo(resolution.source, question, clicked, note=resolution.fact_key)
            self.state.gate_pending = None
        return opened

    def ranked_facts(self, questions: list[ShortQuestion]) -> dict[str, list[str]] | None:
        """Each question's facts, closest by label first; None when ranking is not
        available, which only leaves the model to look through every fact."""
        if self.ctx.fact_ranker is None or not questions:
            return None
        labels = [
            {"key": key, "label": (self.ctx.knowledge.labels.get(key) or key)[:300]}
            for key in sorted(self.ctx.knowledge.facts)
        ]
        try:
            return self.ctx.fact_ranker([item.question for item in questions], labels)
        except (httpx.HTTPError, RuntimeError, ValueError):
            return None

    def gate_in_front(self) -> bool:
        """Whether the tab in front shows a gate on the application's own site."""
        if self.state.form_site is None:
            return False
        try:
            same = registrable_site(self.page.url) == self.state.form_site
            return same and bool(inspect_gateways(self.page))
        except Error:
            return False

    def gate_from_claude(self, gate: dict[str, Any]) -> AnswerResolution | None:
        """One bounded pass: which fact says how you start an application.

        The model only names the fact. What it teaches is checked and saved as a
        pattern, and the gate is answered through the resolver like any choice.
        A dry run writes nothing, so it learns nothing and asks nothing.
        """
        if self.ctx.dry_run:
            return None
        question = str(gate["label"])
        console.print("  [dim]asking Claude which fact says how to start this application[/dim]")
        asked = [ShortQuestion(question, tuple(str(item) for item in gate["options"]))]
        verdicts = self.call_model(self.ctx.short_drafter,
            asked,
            posting={"company": self.ctx.company, "role": self.ctx.title},
            facts=self.ctx.knowledge.for_model(),
            ranked=self.ranked_facts(asked),
            expressions=self.expressions_by_fact(),
        ) or {}
        for verdict in verdicts.values():
            if verdict.kind == "no_fact" and verdict.missing_fact:
                self.state.needed[verdict.question] = verdict.missing_fact
        if not self.learn(verdicts, {question: gate}):
            return None
        return Resolver(self.ctx.knowledge).resolve(gate)

    def record_gate(
        self,
        gate: dict[str, Any],
        *,
        resolution: AnswerResolution | None,
        clicked: str | None,
        opened: bool,
    ) -> None:
        """Write the gate as a fill event, so the question and its proposal are learned."""
        question = str(gate["label"])
        self.persist_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "fill_events": [
                    fill_event(
                        gate,
                        strategy="click_option_button",
                        stopped_at=None if opened else "strategy",
                        operated_ok=opened,
                        value=clicked,
                        resolution=resolution,
                        source=None if resolution is not None else "manual",
                        options=[str(option) for option in gate.get("options") or []],
                    )
                ],
            },
        )
        if clicked and opened:
            self.state.tier_answers[question] = clicked
            self.state.tier_source[question] = (
                resolution.source if resolution is not None else "manual"
            )
            if resolution is not None:
                self.state.tier_resolver[question] = resolution.resolver_id
            self.state.observations.append(
                {
                    "event_index": len(self.state.observations),
                    "step_number": self.state.step,
                    "page_url": self.page.url,
                    "question": question,
                    "answer_text": clicked,
                    "source": self.state.tier_source[question],
                    "form": "short",
                    "control_kind": "choice_group",
                    "field_fingerprint": f"gate:{normalize_answer_question(question)}",
                    "resolver_id": resolution.resolver_id if resolution is not None else None,
                    "proposed_value": resolution.value if resolution is not None else None,
                }
            )

    def note_observed_gate(self) -> None:
        """Learn the way in you clicked yourself, so the next run does not ask."""
        gate = self.state.gate_pending
        self.state.gate_pending = None
        if gate is None:
            return
        clicked = observed_choice(self.page)
        if not clicked:
            return
        self.record_gate(gate, resolution=None, clicked=clicked, opened=True)
        echo("picked up", str(gate["label"]), clicked, note="bind it in the Inbox")

    def emit_warnings(self, fields: list[dict[str, Any]]) -> None:
        resume_fields = [item for item in fields if is_resume_field(item)]
        if len(resume_fields) > 1:
            self.warn_once(
                "multiple-resumes", "multiple resume upload controls; all are left manual."
            )
        if any(item.get("filled") is True for item in fields):
            self.warn_once("profile-drift", "the ATS already prefilled controls; review for drift.")
        knockout = ("sponsor", "authorized to work", "salary", "relocat", "security clearance")
        if any(
            marker in str(item.get("label") or item.get("group") or "").casefold()
            for marker in knockout
            for item in fields
        ):
            self.warn_once("knockout", "review factual or knockout questions carefully.")
