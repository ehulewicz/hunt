"""Driving the visible Chrome: the tab, transitions, filling, readback, capture and evidence.

One module each under this package; this module re-exports their names.
"""

from __future__ import annotations

from personal_os_worker.core.wording import SUBMIT_CONTROL
from personal_os_worker.jobs.browser.capture import (
    UNKNOWN_CONTROLS_SCRIPT,
    VISIBLE_ACTIONS_SCRIPT,
    field_errors,
    inspect_fields,
    inspect_unknown_controls,
    looks_like_application_form,
    visible_actions,
)
from personal_os_worker.jobs.browser.evidence import (
    REDACTED,
    _attribute_escaped,
    page_evidence,
    redact_secrets,
)
from personal_os_worker.jobs.browser.fill import (
    FillObserver,
    _attempt_event,
    _failure,
    _learned_attempts,
    apply_autofill,
    failure_category,
    plan_autofill,
    stopped_stage,
)
from personal_os_worker.jobs.browser.readback import (
    PHONE_LIKE,
    audit_filled_form,
    fresh_control,
    same_entry,
    snapshot_entries,
    snapshot_values,
    verify_control,
)
from personal_os_worker.jobs.browser.tab import (
    POLL_SECONDS,
    SETTLE_SECONDS,
    BrowserClosedError,
    VisibleBrowser,
    _all_frames_text,
    _same_application,
    cdp_target_id,
    identifies_posting,
    is_browser_closed_error,
    registrable_site,
    rendered_body_text,
    tab_continues,
    watch_console,
)
from personal_os_worker.jobs.browser.transitions import (
    CLEAR_OUTLINES_SCRIPT,
    OUTLINE_SCRIPT,
    TRANSITION_ROLES,
    _distinct_destinations,
    click_safe_transition,
    has_submit_control,
    highlight_unfilled,
    reveal_application_form,
)

__all__ = [
    "CLEAR_OUTLINES_SCRIPT",
    "OUTLINE_SCRIPT",
    "PHONE_LIKE",
    "POLL_SECONDS",
    "REDACTED",
    "SETTLE_SECONDS",
    "SUBMIT_CONTROL",
    "TRANSITION_ROLES",
    "UNKNOWN_CONTROLS_SCRIPT",
    "VISIBLE_ACTIONS_SCRIPT",
    "BrowserClosedError",
    "FillObserver",
    "VisibleBrowser",
    "_all_frames_text",
    "_attempt_event",
    "_attribute_escaped",
    "_distinct_destinations",
    "_failure",
    "_learned_attempts",
    "_same_application",
    "apply_autofill",
    "audit_filled_form",
    "cdp_target_id",
    "click_safe_transition",
    "failure_category",
    "field_errors",
    "fresh_control",
    "has_submit_control",
    "highlight_unfilled",
    "identifies_posting",
    "inspect_fields",
    "inspect_unknown_controls",
    "is_browser_closed_error",
    "looks_like_application_form",
    "page_evidence",
    "plan_autofill",
    "redact_secrets",
    "registrable_site",
    "rendered_body_text",
    "reveal_application_form",
    "same_entry",
    "snapshot_entries",
    "snapshot_values",
    "stopped_stage",
    "tab_continues",
    "verify_control",
    "visible_actions",
    "watch_console",
]
