"""The driver: run() pulls every stage together."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from playwright.sync_api import Error

from personal_os_worker.core.tables import console
from personal_os_worker.jobs import model
from personal_os_worker.jobs.autofill_timing import AutofillTimings
from personal_os_worker.jobs.browser import (
    BrowserClosedError,
    is_browser_closed_error,
)
from personal_os_worker.jobs.explain import Explainer
from personal_os_worker.jobs.page_state import ApplicationPageState
from personal_os_worker.jobs.session.audit import AuditMixin
from personal_os_worker.jobs.session.base import SessionBase
from personal_os_worker.jobs.session.capture import CaptureMixin
from personal_os_worker.jobs.session.context import (
    SessionContext,
    SessionState,
    SkippedByUser,
    SubmittedByUser,
)
from personal_os_worker.jobs.session.discovery import DiscoveryMixin
from personal_os_worker.jobs.session.fill import FillMixin
from personal_os_worker.jobs.session.fixer import FixerMixin
from personal_os_worker.jobs.session.learning import LearningMixin
from personal_os_worker.jobs.session.repairs import RepairsMixin
from personal_os_worker.jobs.session.reporting import ReportingMixin
from personal_os_worker.jobs.session.writing import WritingMixin


class AutofillSession(
    ReportingMixin,
    DiscoveryMixin,
    FillMixin,
    LearningMixin,
    WritingMixin,
    AuditMixin,
    RepairsMixin,
    FixerMixin,
    CaptureMixin,
    SessionBase,
):
    def __init__(self, context: SessionContext) -> None:
        self.ctx = context
        self.state = SessionState()
        self.page = context.page
        self._explainer: Explainer | None = None
        self.timings = AutofillTimings(report=lambda line: console.print(line, style="dim"))
        from personal_os_worker.jobs import registry
        from personal_os_worker.jobs.fix import trial

        repo = Path(registry.__file__).resolve().parents[3]
        api = getattr(context, "api", None)
        if api is not None:
            with contextlib.suppress(Exception):
                trial.revert_retired(api, repo)
        self.trial = trial.Trial(
            repo=repo,
            api=api,
            run_id=str(getattr(context, "run_id", "") or ""),
        )
        trial.attach(self.trial)
        model.begin_run()
        self._sent_calls = 0  # the learning and fixing calls the API was last told of

    # ----- driver ----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        console.print(
            "[dim]Ctrl+S records answers for review · Ctrl+A marks submitted and ends · "
            "Ctrl+D skips this listing · Ctrl+C stops and records[/dim]"
        )
        with self.ctx.keys:
            outcome = self._run()
        from personal_os_worker.jobs.fix import trial

        trial.attach(None)
        self.timings.close(model.run_usage())
        return outcome

    def _run(self) -> dict[str, Any]:
        submitted = False
        try:
            while True:
                with self.timings.measure("form_wait", step=self.state.step):
                    fields = self.wait_for_form()
                if fields is None:
                    console.print("The page reports the application was submitted.")
                    submitted = True
                    break
                self.emit_warnings(fields)
                with self.timings.measure("deterministic", step=self.state.step):
                    result = self.fill_deterministic(fields)
                if self.ctx.dry_run:
                    return {"final_url": self.page.url, "answers": [], "dry_run": True}
                with self.timings.measure("revealed", step=self.state.step):
                    self.fill_revealed(fields, result)
                if self.ctx.use_claude:
                    with self.timings.measure("short_drafts", step=self.state.step):
                        self.fill_with_claude(fields, result)
                with self.timings.measure("long_form", step=self.state.step):
                    self.fill_long_form(fields)
                with self.timings.measure("cover_letter_review", step=self.state.step):
                    self.fill_cover_letter(fields)
                with self.timings.measure("audit", step=self.state.step):
                    self.audit_and_repair(fields, result)
                self.report_review()
                with self.timings.measure("user_review", step=self.state.step):
                    transition = self.wait_and_advance(fields)
                if transition == ApplicationPageState.SUBMITTED:
                    console.print("The page reports the application was submitted.")
                    submitted = True
                    break
                self.state.step += 1
        except BrowserClosedError:
            console.print("The browser or application tab was closed. Ending autofill.")
        except SubmittedByUser:
            console.print("Marked as submitted.")
            final_url = self.final_url()
            self.capture_final_snapshot()
            self.save_now()
            return {
                "final_url": final_url,
                "answers": list(self.state.collected.values()),
                "submitted": True,
            }
        except SkippedByUser:
            console.print("Skipped. The listing leaves the board; 'os hunt skipped' lists it.")
            final_url = self.final_url()
            self.capture_final_snapshot()
            self.save_now()
            return {
                "final_url": final_url,
                "answers": list(self.state.collected.values()),
                "skipped": True,
            }
        except KeyboardInterrupt:
            final_url = self.final_url()
            self.capture_final_snapshot()
            self.save_now()
            console.print("Stopped. Saved the current form state.")
            return {
                "final_url": final_url,
                "answers": list(self.state.collected.values()),
                "interrupted": True,
            }
        except Error as error:
            if not is_browser_closed_error(self.page, error):
                raise
            console.print("The browser or application tab was closed. Ending autofill.")
        final_url = self.final_url()
        self.capture_final_snapshot()
        self.save_now()
        return {
            "final_url": final_url,
            "answers": list(self.state.collected.values()),
            "submitted": submitted,
        }

    def answers_so_far(self) -> list[dict[str, Any]]:
        return list(self.state.collected.values())
