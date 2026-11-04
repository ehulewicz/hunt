"""Reporting: model-call accounting, failure rows, and evidence uploads."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

import httpx
from playwright.sync_api import Error

from personal_os_worker.core.config import console
from personal_os_worker.jobs import model
from personal_os_worker.jobs.browser import (
    inspect_fields,
    page_evidence,
)
from personal_os_worker.jobs.model_errors import ModelError
from personal_os_worker.jobs.page_state import (
    ApplicationPageState,
    classify_application_page,
)
from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.context import (
    SkippedByUser,
    SubmittedByUser,
    content_type_for,
    echo,
)
from personal_os_worker.jobs.sites import failure_signature


class ReportingMixin(SessionBase):
    def model_calls(self) -> dict[str, int]:
        """This run's model calls by purpose, the processes it reloaded from included."""
        return model.run_counts()

    def expressions_by_fact(self) -> dict[str, list[str]]:
        """The active expression branches each fact already has, for the prompt."""
        grouped: dict[str, list[str]] = {}
        for pattern in self.ctx.knowledge.patterns:
            if (
                pattern.get("kind") == "expression"
                and pattern.get("status", "active") == "active"
                and pattern.get("expression")
            ):
                grouped.setdefault(str(pattern["fact_key"]), []).append(
                    str(pattern["expression"])
                )
        return grouped

    def learning_calls(self) -> int:
        """The calls a site seen before should not need again: pattern and code."""
        calls = self.model_calls()
        return calls["pattern"] + calls["code"]

    def call_model[T](self, operation: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:
        """Keep model outages visible without ending the live application."""
        if self.state.model_error is not None:
            return None
        try:
            return operation(*args, **kwargs)
        except (ModelError, subprocess.TimeoutExpired, OSError) as error:
            self.state.model_error = str(error) if isinstance(error, ModelError) else (
                "Claude timed out or could not start; drafting and answer checks unavailable"
            )
            echo("failed", "Drafting and answer audit", note=self.state.model_error)
            return None

    # ----- reporting -------------------------------------------------------

    def persist_step(self, run_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Attach the exact inputs used for each attempt before storing its observation."""
        payload = dict(payload)
        payload["model_calls"] = self._sent_calls = self.learning_calls()
        payload["model_usage"] = model.run_usage()
        if "fill_events" in payload:
            knowledge = self.ctx.knowledge
            snapshot = {"facts": knowledge.facts, "types": knowledge.types, "seed": knowledge.seed}
            payload["fill_events"] = [
                event | {"knowledge": snapshot} for event in payload["fill_events"]
            ]
        return self.ctx.api.persist_autofill_step(run_id, payload)

    def upload_evidence(self, evidence: dict[str, bytes]) -> dict[str, str]:
        with self.timings.measure("evidence_upload", step=self.state.step):
            return self._upload_evidence(evidence)

    def _upload_evidence(self, evidence: dict[str, bytes]) -> dict[str, str]:
        """Upload what can be uploaded; evidence is never worth ending the session over."""
        paths: dict[str, str] = {}
        for name, content in evidence.items():
            try:
                paths[name] = self.ctx.api.upload_autofill_artifact(
                    self.ctx.run_id, name=name, content=content,
                    content_type=content_type_for(name),
                )
            except httpx.HTTPError as error:
                self.warn_once(
                    "evidence-upload",
                    f"Could not store failure evidence ({error.__class__.__name__}); "
                    "the fill carries on without it.",
                )
        return paths

    def report_page_failure(
        self,
        category: str,
        message: str,
        *,
        artifact_paths: dict[str, str] | None = None,
        offered: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if artifact_paths is None:
            captured = inspect_fields(self.page)
            artifact_paths = self.upload_evidence(
                page_evidence(self.page, captured, console_errors=self.ctx.console_errors)
            )
        fingerprint = f"{category}|{self.page.url.split('?', 1)[0]}|{self.state.step}"
        return self.persist_step(
            self.ctx.run_id,
            {
                "step_number": self.state.step,
                "url": self.page.url,
                "failures": [
                    {
                        "signature": failure_signature(
                            self.ctx.ats, category, fingerprint
                        ),
                        "category": category,
                        "field_fingerprint": fingerprint,
                        "error_message": message,
                        "artifact_paths": artifact_paths,
                        "dom_signals": {"offered": offered} if offered else {},
                    }
                ],
            },
        )

    def warn_once(self, key: str, message: str) -> None:
        if key not in self.state.warnings:
            console.print(f"[yellow]Warning:[/yellow] {message}")
            self.state.warnings.add(key)

    def sleep(self) -> None:
        if self.ctx.link is not None:
            self.ctx.link.tab(self.page)
        self.safe_point()
        pressed = self.ctx.keys.wait(self.ctx.poll_seconds)
        if "save" in pressed:
            self.save_now()
        if "done" in pressed:
            raise SubmittedByUser
        if "skip" in pressed:
            raise SkippedByUser
        if "guide" in pressed:
            self.take_direction()

    def take_direction(self) -> None:
        """Ask at the terminal for a direction to the fixer, and say where it went."""
        if self.ctx.fixes is None:
            console.print("  [dim]the fixer is off in this run[/dim]")
            return
        text = self.ctx.keys.read_line("Direction for the fixer: ")
        if text.strip():
            console.print(f"  [dim]{self.ctx.fixes.guide(text)}[/dim]")

    def form_is_gone(self) -> bool:
        """The page has left the form for a confirmation, so nothing on it is ours."""
        try:
            fields = inspect_fields(self.page)
        except Error:
            return False
        return (
            classify_application_page(self.page, fields, self.state.navigation)
            == ApplicationPageState.SUBMITTED
        )

    def save_now(self) -> int:
        with self.timings.measure("history_checkpoint", step=self.state.step):
            return self._save_now()

    def _save_now(self, *, quiet: bool = False) -> int:
        """Persist newly observed values for this run without approving reuse."""
        pending = self.state.observations[self.state.persisted_observations :]
        for offset in range(0, len(pending), 500):
            batch = pending[offset : offset + 500]
            self.persist_step(
                self.ctx.run_id,
                {
                    "step_number": self.state.step,
                    "url": self.page.url,
                    "observations": batch,
                },
            )
            self.state.persisted_observations += len(batch)
        if not pending and self.learning_calls() > self._sent_calls:
            # A call since the last step, the fixer's in the background most often.
            self.persist_step(
                self.ctx.run_id,
                {"step_number": self.state.step, "url": self.page.url, "observations": []},
            )
        if not quiet:
            console.print(
                f"  [dim]recorded {len(pending)} answer observation(s) for review[/dim]"
            )
        return len(pending)
