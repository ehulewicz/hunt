"""The Jobs domain: browse listings, inspect applications, queue preparation."""

from __future__ import annotations

from personal_os_worker.api import WorkerApi
from personal_os_worker.commands.services import connected
from personal_os_worker.doctor import run_doctor
from personal_os_worker.runner import run_worker
from personal_os_worker.service import ensure_chrome
from personal_os_worker.tables import (
    console,
    render_answers,
    render_applications,
    render_listings,
    resolve,
)

DEFAULT_TECHNICAL_FAMILIES = [
    "software_engineering",
    "data_engineering",
    "ml_engineering",
    "data_science",
]


def run_listings(
    *, category: str | None, locations: list[str], role_families: list[str], limit: int
) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        page = api.listings(
            category=category,
            locations=locations,
            role_families=role_families,
            limit=limit,
        )
    finally:
        api.close()
    items = page.get("items") or []
    if not items:
        console.print("No listings matched.")
        return 0
    render_listings(items, int(page.get("total", len(items))))
    return 0


def run_sync() -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        result = api.sync_listings()
    finally:
        api.close()
    console.print(
        f"Synced {result.get('open', 0)} open listings from "
        f"{result.get('source', 'the configured source')} "
        f"({result.get('seen', 0)} rows seen)."
    )
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


def run_answers() -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        items = api.answers()
    finally:
        api.close()
    render_answers(items)
    return 0


def _answer_text(*, text: str | None, file: str | None) -> str:
    if file:
        from pathlib import Path

        try:
            value = Path(file).expanduser().read_text(encoding="utf-8")
        except FileNotFoundError:
            raise RuntimeError(f"Answer file not found: {file}") from None
    else:
        value = text or ""
    value = value.strip()
    if not value:
        raise RuntimeError("Answer text cannot be empty")
    return value


def run_answer_add(
    *, question: str, text: str | None, file: str | None, approved: bool
) -> int:
    answer_text = _answer_text(text=text, file=file)
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        item = api.create_answer(
            question=question.strip(), answer_text=answer_text, approved=approved
        )
    finally:
        api.close()
    console.print(
        f"Saved answer {str(item.get('id', ''))[:8]} as "
        f"{'approved' if item.get('approved') else 'draft'}."
    )
    return 0


def run_answer_edit(
    reference: str,
    *,
    question: str | None,
    text: str | None,
    file: str | None,
    approved: bool | None,
) -> int:
    changes: dict[str, object] = {}
    if question is not None:
        changes["question"] = question.strip()
    if text is not None or file is not None:
        changes["answer_text"] = _answer_text(text=text, file=file)
    if approved is not None:
        changes["approved"] = approved
    if not changes:
        raise RuntimeError("Choose a question, answer text/file, or approval change")
    answer_id = resolve("answers", reference)
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        item = api.update_answer(answer_id, changes)
    finally:
        api.close()
    console.print(
        f"Updated answer {str(item.get('id', ''))[:8]}; "
        f"state is {'approved' if item.get('approved') else 'draft'}."
    )
    return 0


def run_prep(references: list[str], *, tailor: bool) -> int:
    config, token = connected()
    posting_ids = [resolve("listings", reference) for reference in references]
    api = WorkerApi(config, token)
    try:
        result = api.batch_prepare(posting_ids, tailor=tailor)
    finally:
        api.close()
    queued = result.get("queued_task_ids") or []
    console.print(
        f"Queued {len(queued)} preparation task(s) for "
        f"{len(result.get('application_ids') or [])} application(s)."
    )
    console.print("Run 'os hunt run' to execute them.")
    if tailor:
        console.print("Claude tailoring requested; the 10% resume guard remains enforced.")
    else:
        console.print("Deterministic mode: the canonical resume will be compiled unchanged.")
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
    provider: str,
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
        audit_filled_form,
        inspect_fields,
        looks_like_application_form,
        plan_autofill,
        reveal_application_form,
    )

    config, token = connected()
    ensure_chrome(config.cdp_url)
    if provider == "lentra":
        with VisibleBrowser(config.cdp_url) as browser:
            page = browser.open_page(url) if url else browser.active_page()
            reveal_application_form(page)
            before = inspect_fields(page)
            if not looks_like_application_form(before):
                console.print(
                    f"[red]No application form here[/red] ({len(before)} control(s) found). "
                    "Navigate to the real form and rerun without --url."
                )
                return 1
            console.print(f"Target: [cyan]{page.url}[/cyan]")
            console.print(
                "Lentra handoff: click Lentra's Fill control in Windows Chrome, review its "
                "drafts, then return here. Lentra syncs your profile/resume to its service "
                "and may use third-party AI for free-text answers."
            )
            input("Press Enter after Lentra has finished filling (Ctrl+C to cancel): ")
            audit = audit_filled_form(inspect_fields(page))
        console.print(
            f"Audited {len(audit['filled_fields'])} filled field(s); "
            f"{len(audit['required_unfilled'])} required field(s) remain empty."
        )
        for label in audit["required_unfilled"]:
            console.print(f"  [yellow]required and empty:[/yellow] {label}")
        for label in audit["sensitive_filled"]:
            console.print(f"  [red]review sensitive field filled by Lentra:[/red] {label}")
        console.print("Nothing was submitted. Review the visible form and submit it yourself.")
        return 0

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
