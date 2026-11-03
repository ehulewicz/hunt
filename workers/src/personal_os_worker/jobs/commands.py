"""The Jobs domain: browse listings, track what went in, and check this machine."""

from __future__ import annotations

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.commands import connected
from personal_os_worker.core.config import console, pick
from personal_os_worker.core.doctor import run_doctor
from personal_os_worker.core.secrets import secrets_check
from personal_os_worker.core.service import open_external_url
from personal_os_worker.jobs.tables import render_applications, render_listings

APPLICATION_STATUSES = (
    "applying", "applied", "oa", "oa_completed", "interview", "offer", "rejected", "skipped"
)

DEFAULT_TECHNICAL_FAMILIES = [
    "software_engineering",
    "data_engineering",
    "ml_engineering",
]


def run_listings(
    *,
    category: str | None,
    locations: list[str],
    role_families: list[str],
    ats: str | None,
    limit: int,
) -> int:
    """The board. Rows keep the number the database assigned, whatever is filtered out."""
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        page = api.listings(
            category=category,
            locations=locations,
            role_families=role_families,
            # The form type is read off the URL here, so filter after a full fetch.
            limit=1000 if ats else limit,
        )
    finally:
        api.close()
    items = page.get("items") or []
    total = int(page.get("total", len(items)))
    if ats:
        items = by_ats(items, ats)
        total = len(items)
        items = items[:limit]
    if not items:
        console.print("No listings matched.")
        return 0
    render_listings(items, total, title=f"Listings · {ats}" if ats else "Listings")
    return 0


def by_ats(items: list[dict[str, object]], ats: str) -> list[dict[str, object]]:
    """Listings whose direct application URL is on this form type."""
    from personal_os_worker.jobs.sites import detect_ats

    return [
        item for item in items if detect_ats(str(item.get("application_url") or "")) == ats
    ]


def today_listings(
    api: WorkerApi, *, role_families: list[str], ats: str | None
) -> list[dict[str, object]]:
    """Listings at most a day old, same rows and numbering as `list`, newest board order.

    The feed dates postings by day and syncs once a morning, so "today" is
    anything at most a day old; at 0d only, the view is empty every morning.
    `ats` narrows to one form type by the direct application URL.
    """
    page = api.listings(role_families=role_families, limit=500)
    items = [
        item
        for item in page.get("items") or []
        if item.get("source_age_days") is not None and int(item["source_age_days"]) <= 1
    ]
    if ats:
        items = by_ats(items, ats)
    return items


def run_today(*, role_families: list[str], ats: str | None) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        items = today_listings(api, role_families=role_families, ats=ats)
    finally:
        api.close()
    if not items:
        where = f" on {ats}" if ats else ""
        console.print(f"Nothing new today{where} that you have not already applied to.")
        return 0
    render_listings(items, len(items), title=f"Today · {ats}" if ats else "Today")
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


def run_open_listing(reference: str, *, source: bool) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        record = api.listing(reference)
    finally:
        api.close()
    key = "discovery_url" if source else "application_url"
    url = str(record.get(key) or "").strip()
    if not url:
        label = "discovery" if source else "direct application"
        raise RuntimeError(f"This listing has no {label} URL")
    open_external_url(url)
    console.print(f"Opened {record.get('company_name') or 'listing'}: {url}")
    return 0


def run_apps(reference: str | None = None, status: str | None = None) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        applications = [item for item in api.applications() if item.get("status") not in HIDDEN]
        if reference is not None:
            application = pick(applications, reference, kind="applications")
            if status is None:
                choices = ", ".join(APPLICATION_STATUSES)
                raise RuntimeError(f"Give the new status, one of: {choices}")
            updated = api.update_application(str(application["id"]), status=status)
            console.print(f"{updated.get('company_name')}: {status}")
            applications = [
                item for item in api.applications() if item.get("status") not in HIDDEN
            ]
    finally:
        api.close()
    if not applications:
        console.print("Nothing applied yet. 'os hunt apply <row>' records one when it goes in.")
        return 0
    render_applications(applications)
    return 0


# Skipped listings have their own restore workflow; `apps` is the lifecycle.
HIDDEN = frozenset({"skipped"})


def run_skipped(restore: str | None = None) -> int:
    """Listings passed over with Ctrl+D; `restore <n>` puts one back on the board."""
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        skipped = [item for item in api.applications() if item.get("status") == "skipped"]
        if restore is not None:
            application = pick(skipped, restore, kind="skipped listings")
            # Archiving the application is what the board ignores, so the listing returns.
            api.archive_application(str(application["id"]))
            console.print(
                f"Restored {application.get('company_name')} — {application.get('title')} "
                "to the board."
            )
            skipped = [item for item in api.applications() if item.get("status") == "skipped"]
    finally:
        api.close()
    if not skipped:
        console.print("Nothing skipped. Ctrl+D during 'os hunt apply' skips a listing.")
        return 0
    render_applications(skipped, title="Skipped")
    return 0


def run_doctor_command() -> int:
    from personal_os_worker.jobs.readiness import fixer_check

    config, token = connected()
    checks = run_doctor(config, token, extra=(secrets_check, fixer_check))
    for check in checks:
        console.print(f"[{check.label}] {check.name}: {check.detail}", markup=False)
    return 0 if all(check.ok or check.advisory for check in checks) else 1
