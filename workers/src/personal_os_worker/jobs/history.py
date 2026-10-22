"""What changed, what the fixer did, and which sites still need a model.

`os hunt changes` reads the change history: every change to a pattern, fact,
prose, question, strategy, or code, who made it, the failure it fixed, and how
its replay went. `os hunt fixes` lists the failures the fixer took on: being
fixed, fixed by a commit, or unsolved with what it tried. `os hunt sites` shows
each site's latest run and its model calls: a site whose latest run made none
has met the goal, and the next application there needs no model either.
All three only read.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from rich import box
from rich.markup import escape
from rich.syntax import Syntax
from rich.table import Table

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.commands import connected
from personal_os_worker.core.tables import console, pick, short_id

CHANGE_SUBJECTS = ("pattern", "fact", "code", "prose", "question", "strategy")
CHANGE_STATUSES = ("pending", "passed", "failed")
DEFAULT_CHANGES = 20
# The API's page; `show` resolves a row or prefix against this many, newest first,
# so its row numbers are the list's.
MAX_CHANGES = 500
FIX_STATUSES = ("fixing", "fixed", "unsolved")
STATUS_STYLES = {
    "passed": "green", "failed": "red", "pending": "yellow",
    "fixed": "green", "fixing": "yellow", "unsolved": "red",
}
# Snapshot keys that say nothing about what changed.
QUIET_KEYS = frozenset({"id", "diff", "created_at", "updated_at", "verified_at"})


def table(title: str) -> Table:
    return Table(
        title=title, header_style="bold", box=box.SIMPLE_HEAD, padding=(0, 1), expand=True
    )


def _when(value: Any) -> str:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value or "-")[:16]
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def _styled(status: Any) -> str:
    text = str(status or "-")
    style = STATUS_STYLES.get(text)
    return f"[{style}]{text}[/{style}]" if style else escape(text)


def _compact(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)


def _snapshot(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(
            f"{key} {_compact(item)}" for key, item in value.items()
            if key not in QUIET_KEYS and item not in (None, "", [], {})
        ) or "-"
    return _compact(value)


def subject_label(change: dict[str, Any]) -> str:
    """The subject and its id: a fact by key, a commit short, anything else by short id."""
    subject = str(change.get("subject") or "")
    subject_id = str(change.get("subject_id") or "")
    if subject == "fact":
        return f"fact {subject_id}"
    if subject == "code":
        return f"code {subject_id[:7]}"
    return f"{subject} {short_id(subject_id)}"


def before_after(change: dict[str, Any]) -> str:
    """What a change did, in one line: the fields that differ, or the one side there is."""
    before, after = change.get("before"), change.get("after")
    if change.get("subject") == "code":
        parent = (before or {}).get("commit") if isinstance(before, dict) else None
        commit = str(change.get("subject_id") or "")
        summary = (after or {}).get("summary") if isinstance(after, dict) else None
        line = f"{str(parent)[:7] if parent else '-'} → {commit[:7]}"
        return f"{line} {summary}" if summary else line
    if isinstance(before, dict) and isinstance(after, dict):
        keys = [
            key for key in dict.fromkeys([*before, *after])
            if key not in QUIET_KEYS and before.get(key) != after.get(key)
        ]
        if keys:
            return ", ".join(
                f"{key} {_compact(before.get(key))} → {_compact(after.get(key))}" for key in keys
            )
    return f"{_snapshot(before)} → {_snapshot(after)}"


def failure_fixed(change: dict[str, Any]) -> str:
    """The failure a change answered: its run, the issue, and the question's key."""
    parts = []
    if change.get("run_id"):
        parts.append(f"run {short_id(str(change['run_id']))}")
    if change.get("failure_issue_id"):
        parts.append(f"issue {short_id(str(change['failure_issue_id']))}")
    if change.get("question_key"):
        parts.append(str(change["question_key"]))
    return " ".join(parts) or "-"


def verification(change: dict[str, Any]) -> str:
    status = change.get("status")
    if status is None:
        return "-"
    if change.get("sampled") is None:
        return _styled(status)
    return f"{_styled(status)} {change.get('failed')}/{change.get('sampled')}"


def render_changes(changes: list[dict[str, Any]]) -> None:
    view = table(f"Changes ({len(changes)}, newest first)")
    view.add_column("#", justify="right", style="dim")
    view.add_column("id", style="dim", no_wrap=True)
    view.add_column("when", no_wrap=True)
    view.add_column("subject", no_wrap=True, overflow="ellipsis", max_width=36)
    view.add_column("action", no_wrap=True)
    view.add_column("who", no_wrap=True)
    view.add_column("fixed", no_wrap=True, overflow="ellipsis", max_width=48, style="dim")
    view.add_column("check", no_wrap=True)
    view.add_column("before → after", no_wrap=True, overflow="ellipsis", ratio=1)
    for index, change in enumerate(changes, start=1):
        view.add_row(
            str(index),
            short_id(str(change.get("id", ""))),
            _when(change.get("created_at")),
            escape(subject_label(change)),
            str(change.get("action") or "-"),
            str(change.get("origin") or "-"),
            escape(failure_fixed(change)),
            verification(change),
            escape(before_after(change)),
        )
    console.print(view)


def show_change(change: dict[str, Any]) -> None:
    """One change in full: every field, both sides, and a code change's diff."""
    lines = [
        ("change", str(change.get("id"))),
        ("when", _when(change.get("created_at"))),
        ("subject", f"{change.get('subject')} {change.get('subject_id')}"),
        ("action", str(change.get("action"))),
        ("who", str(change.get("origin"))),
        ("fixed", failure_fixed(change)),
    ]
    for label, value in lines:
        console.print(f"[bold]{label:<8}[/bold] {escape(value)}")
    check = verification(change)
    if change.get("verified_at"):
        check += f" (verified {_when(change['verified_at'])})"
    console.print(f"[bold]{'check':<8}[/bold] {check}")
    after = change.get("after")
    diff = after.get("diff") if isinstance(after, dict) else None
    for label, side in (("before", change.get("before")), ("after", after)):
        console.print(f"[bold]{label}[/bold]")
        if isinstance(side, dict) and diff:
            side = {key: item for key, item in side.items() if key != "diff"}
        if side is None:
            console.print("  -")
        else:
            console.print_json(json.dumps(side, default=str))
    if diff:
        console.print("[bold]diff[/bold]")
        console.print(Syntax(str(diff), "diff", word_wrap=True))


def run_changes(
    action: str | None,
    reference: str | None,
    *,
    subject: str | None = None,
    status: str | None = None,
) -> int:
    """`changes [N]` lists the newest N; `changes show <row|id-prefix>` prints one."""
    if action == "show":
        if reference is None:
            raise RuntimeError("'changes show' needs a row from 'os hunt changes' or an id prefix")
        limit = MAX_CHANGES
    elif reference is not None:
        raise RuntimeError("Use: os hunt changes [N] | os hunt changes show <row|id-prefix>")
    elif action is None:
        limit = DEFAULT_CHANGES
    elif action.isdigit() and int(action) > 0:
        limit = min(int(action), MAX_CHANGES)
    else:
        raise RuntimeError(f"'changes' takes a number of rows or 'show', not {action!r}")
    filters: dict[str, str | int] = {"limit": limit}
    if subject:
        filters["subject"] = subject
    if status:
        filters["status"] = status
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        changes = api.changes(**filters)
    finally:
        api.close()
    if reference is not None:
        show_change(pick(changes, reference, kind="changes"))
        return 0
    if not changes:
        console.print("No changes recorded" + (" that match." if len(filters) > 1 else "."))
        return 0
    render_changes(changes)
    return 0


def render_fixes(issues: list[dict[str, Any]]) -> None:
    view = table(f"Fixes ({len(issues)})")
    view.add_column("#", justify="right", style="dim")
    view.add_column("id", style="dim", no_wrap=True)
    view.add_column("category", no_wrap=True)
    view.add_column("question", overflow="ellipsis", ratio=2)
    view.add_column("status", no_wrap=True)
    view.add_column("seen", justify="right", no_wrap=True)
    view.add_column("commit", no_wrap=True)
    view.add_column("notes", ratio=3)
    for index, issue in enumerate(issues, start=1):
        view.add_row(
            str(index),
            short_id(str(issue.get("id", ""))),
            escape(str(issue.get("category") or "-")),
            escape(str(issue.get("field_label") or "-")),
            _styled(issue.get("status")),
            f"x{issue.get('occurrence_count') or 0}",
            str(issue.get("fixed_commit") or "-")[:7],
            escape(str(issue.get("verification_notes") or "-")),
        )
    console.print(view)


def run_fixes() -> int:
    """The failures the fixer took on, and how each one went."""
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        issues = [
            issue for issue in api.autofill_failures() if issue.get("status") in FIX_STATUSES
        ]
    finally:
        api.close()
    if not issues:
        console.print("The fixer has not taken on a failure yet.")
        return 0
    render_fixes(issues)
    return 0


def render_sites(sites: list[dict[str, Any]]) -> None:
    met = sum(1 for site in sites if site.get("model_calls") == 0)
    view = table(f"Sites ({met} of {len(sites)} need no model calls)")
    view.add_column("site", no_wrap=True, overflow="ellipsis", ratio=1)
    view.add_column("runs", justify="right", no_wrap=True)
    view.add_column("latest", no_wrap=True)
    view.add_column("model calls", justify="right", no_wrap=True)
    view.add_column("open", justify="right", no_wrap=True)
    view.add_column("unsolved", justify="right", no_wrap=True)
    view.add_column("goal", no_wrap=True)
    for site in sites:
        latest = _when(site.get("last_run_at"))
        if site.get("run_status") == "running":
            latest += " (running)"
        calls = site.get("model_calls")
        view.add_row(
            escape(str(site.get("site") or "-")),
            str(site.get("runs") or 0),
            latest,
            str(calls),
            str(site.get("open_issues") or 0),
            str(site.get("unsolved_issues") or 0),
            "[green]met[/green]" if calls == 0 else "",
        )
    console.print(view)


def run_sites() -> int:
    """Each site's latest run: a site whose latest run made no model calls has met the
    goal that the next application there needs none."""
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        sites = api.autofill_sites()
    finally:
        api.close()
    if not sites:
        console.print("No runs yet.")
        return 0
    render_sites(sites)
    return 0
