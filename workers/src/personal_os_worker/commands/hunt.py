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


def run_tasks(*, once: bool) -> int:
    config, token = connected()
    # Tasks drive the visible browser, so Chrome is a dependency too.
    ensure_chrome(config.cdp_url)
    return run_worker(config, token, once=once)


def run_doctor_command() -> int:
    config, token = connected()
    checks = run_doctor(config, token)
    for check in checks:
        console.print(f"[{'OK' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1
