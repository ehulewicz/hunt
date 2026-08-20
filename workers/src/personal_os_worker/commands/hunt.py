"""The Jobs domain: browse listings, inspect applications, queue preparation."""

from __future__ import annotations

from personal_os_worker.api import WorkerApi
from personal_os_worker.commands.services import connected
from personal_os_worker.doctor import run_doctor
from personal_os_worker.runner import run_worker
from personal_os_worker.service import ensure_chrome
from personal_os_worker.tables import (
    console,
    render_applications,
    render_listings,
    resolve,
)


def run_listings(*, category: str | None, locations: list[str], limit: int) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        page = api.listings(category=category, locations=locations, limit=limit)
    finally:
        api.close()
    items = page.get("items") or []
    if not items:
        console.print("No listings matched.")
        return 0
    render_listings(items, int(page.get("total", len(items))))
    return 0


def run_apps() -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        applications = api.applications()
    finally:
        api.close()
    if not applications:
        console.print("No applications yet. Prepare one with 'os hunt prep <row>'.")
        return 0
    render_applications(applications)
    return 0


def run_prep(references: list[str]) -> int:
    config, token = connected()
    posting_ids = [resolve("listings", reference) for reference in references]
    api = WorkerApi(config, token)
    try:
        result = api.batch_prepare(posting_ids)
    finally:
        api.close()
    queued = result.get("queued_task_ids") or []
    console.print(
        f"Queued {len(queued)} preparation task(s) for "
        f"{len(result.get('application_ids') or [])} application(s)."
    )
    console.print("Run 'os hunt run' to execute them.")
    return 0


def run_tasks(*, once: bool, watch: bool = False) -> int:
    config, token = connected()
    # Tasks drive the visible browser, so Chrome is a dependency too.
    ensure_chrome(config.cdp_url)
    return run_worker(config, token, once=once, watch=watch)


def run_doctor_command() -> int:
    config, token = connected()
    checks = run_doctor(config, token)
    for check in checks:
        console.print(f"[{'OK' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


def run_autofill(
    *,
    url: str | None,
    dry_run: bool,
    resume: str | None,
) -> int:
    """Autofill in isolation, against a page you chose.

    The task path always opens its own tab from a stored URL, which makes the
    filling logic impossible to exercise on a form you navigated to by hand.
    """
    from pathlib import Path

    from rich.table import Table

    from personal_os_worker.browser import (
        VisibleBrowser,
        apply_autofill,
        looks_like_application_form,
        plan_autofill,
    )

    config, token = connected()
    ensure_chrome(config.cdp_url)
    api = WorkerApi(config, token)
    try:
        profile = api.profile()
        approved_answers = [
            answer for answer in api.answers() if answer.get("status") == "approved"
        ]
    finally:
        api.close()

    resume_path = Path(resume).expanduser() if resume else None
    if resume_path is not None and not resume_path.is_file():
        console.print(f"[red]Resume not found:[/red] {resume_path}")
        return 1

    with VisibleBrowser(config.cdp_url) as browser:
        page = browser.open_page(url) if url else browser.active_page()
        console.print(f"Target: [cyan]{page.url}[/cyan]")
        fields, plan = plan_autofill(
            page, profile=profile, approved_answers=approved_answers
        )
        if not looks_like_application_form(fields):
            console.print(
                f"[red]No application form here[/red] ({len(fields)} control(s) found). "
                "The posting may keep the form behind a step this could not reach. "
                "The page is left open; navigate to the form and rerun without --url."
            )
            return 1
        console.print(
            f"Found {len(fields)} control(s); {len(plan.fills)} would be filled, "
            f"{len(plan.unanswered_required_questions)} required question(s) unanswered."
        )

        table = Table(box=None, padding=(0, 2), expand=True)
        table.add_column("#", justify="right", style="dim")
        table.add_column("field", no_wrap=True, overflow="ellipsis")
        table.add_column("kind", no_wrap=True)
        table.add_column("value", overflow="fold", ratio=1)
        for fill in plan.fills:
            field = fields[fill.index]
            label = str(field.get("label") or field.get("name") or "")
            value = "<resume file>" if fill.kind == "resume" else (fill.value or "")
            table.add_row(str(fill.index), label, fill.kind, value)
        if plan.fills:
            console.print(table)

        for question in plan.unanswered_required_questions:
            console.print(f"  [yellow]unanswered required:[/yellow] {question}")

        if dry_run:
            console.print("\n[dim]Dry run: the page was not modified.[/dim]")
            return 0

        result = apply_autofill(
            page,
            fields,
            plan,
            resume_path=resume_path,
            assert_active=lambda: None,
        )

    console.print(f"\nFilled {len(result['filled_fields'])} field(s). Nothing was submitted.")
    for skipped in result["skipped_fields"]:
        console.print(f"  [dim]skipped:[/dim] {skipped}")
    for question in result["unanswered_required_questions"]:
        console.print(f"  [yellow]left for you:[/yellow] {question}")
    return 0
