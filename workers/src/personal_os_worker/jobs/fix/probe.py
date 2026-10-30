"""Trying a fix on the live control, from a process running the fix's own code.

`python -m personal_os_worker.jobs.fix.probe ARGS.json`, started with the
scratch clone's source first on `PYTHONPATH`, attaches to the run's Chrome over
CDP, finds the run's tab, arms the submit guards, and operates only the target:

- a field: captured with the fix's `inspect_fields`, answered from a fact by the
  resolver, operated by the fix's strategies, and read back (the repair lane's
  `repair_strategy`). A control the fix now captures that no fact answers yet
  counts as detected, and fills once a pattern names its fact;
- a posting or a form: the site's route, then the navigator or the Apply
  control, read back as the page naming the role, or a form appearing.

It prints one JSON result on stdout and never submits, signs in, or clicks Next.
"""

from __future__ import annotations

import json
import sys
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Page, sync_playwright

from personal_os_worker.jobs.resolve import Knowledge


def knowledge_of(data: dict[str, Any]) -> Knowledge:
    """The run's knowledge as the session wrote it (`dataclasses.asdict`)."""
    names = {item.name for item in dataclass_fields(Knowledge)}
    values = {key: value for key, value in data.items() if key in names}
    for name in ("patterns", "prose"):
        values[name] = tuple(values.get(name) or ())
    return Knowledge(**values)


def find_page(browser: Browser, target_id: str | None, url: str) -> Page | None:
    """The run's tab, by its CDP target id, else by its URL."""
    from personal_os_worker.jobs.browser import cdp_target_id

    pages = [page for context in browser.contexts for page in context.pages]
    if target_id:
        for page in pages:
            if cdp_target_id(page) == target_id:
                return page
        return None
    return next((page for page in pages if page.url == url), None)


def probe_field(page: Page, target: dict[str, Any], knowledge: Knowledge) -> dict[str, Any]:
    from personal_os_worker.jobs.autofill import offered_options
    from personal_os_worker.jobs.browser import inspect_fields
    from personal_os_worker.jobs.registry.kinds import classify
    from personal_os_worker.jobs.repair.repairer import find_field, repair_strategy
    from personal_os_worker.jobs.resolve import Resolver
    from personal_os_worker.jobs.safety import refuse_submit

    question = str(target["question"])
    fields = inspect_fields(page)
    field = find_field(fields, question)
    if field is None:
        return {"ok": False, "note": f"the fix's code still does not capture {question!r}"}
    refuse_submit(field)
    kind = classify(field)
    resolution = Resolver(knowledge).resolve(
        dict(field) | {"label": question, "options": offered_options(fields, field)}
    )
    if resolution is None or resolution.value is None:
        return {"ok": True, "filled": False, "kind": kind,
                "note": f"captured as {kind}; no fact answers it yet"}
    candidate = repair_strategy(page, field, str(resolution.value))
    if candidate is None:
        return {"ok": False, "kind": kind,
                "note": f"captured as {kind}, but no strategy read back {resolution.value!r}"}
    return {"ok": True, "filled": True, "kind": kind, "value": candidate.get("value"),
            "strategy": candidate.get("strategy"),
            "note": f"{candidate.get('strategy')} read back {candidate.get('value')!r}"}


def probe_route(page: Page, target: dict[str, Any]) -> dict[str, Any]:
    from personal_os_worker.jobs.browser import (
        inspect_fields,
        looks_like_application_form,
        reveal_application_form,
    )
    from personal_os_worker.jobs.navigate import Target, find_posting
    from personal_os_worker.jobs.registry.routes import route_for

    goal = str(target.get("goal") or "posting")
    wanted = Target(str(target["title"]), str(target["company"]))
    route = route_for(page.url)
    if goal == "posting":
        found = find_posting(page, wanted, route=route)
        return {"ok": found, "note": "the posting is open" if found
                else "neither the route nor the navigator reached the posting"}
    if route is not None:
        route(page, wanted)
    if not looks_like_application_form(inspect_fields(page)):
        reveal_application_form(page)
    opened = looks_like_application_form(inspect_fields(page))
    return {"ok": opened, "note": "the form is open" if opened else "no form appeared"}


def probe_candidate(
    page: Page, target: dict[str, Any], knowledge: Knowledge, declarations: list[Any],
) -> dict[str, Any]:
    """The candidate file's declarations, verified on the live page."""
    from personal_os_worker.jobs.registry.api import (
        Format,
        Kind,
        QuestionReader,
        Route,
        Strategy,
    )
    from personal_os_worker.jobs.registry.kinds import _as_custom
    from personal_os_worker.jobs.registry.kinds.custom import capture, read

    for declaration in declarations:
        if isinstance(declaration, Kind):
            custom = _as_custom(declaration)
            for frame in page.frames:
                found = capture(frame, custom)
                if not found:
                    continue
                control = frame.locator(custom.selector).nth(
                    int(found[0].get("custom_index") or 0)
                )
                held = read(control, custom)
                return {"ok": True, "kind": declaration.name,
                        "note": f"captured and read {held.get('value')!r}"}
            return {"ok": False, "note": "the kind captured nothing on the page"}
        if isinstance(declaration, (Strategy, Format)):
            return probe_field(page, target, knowledge)
        if isinstance(declaration, QuestionReader):
            fields = probe_question(page, declaration)
            return fields
        if isinstance(declaration, Route):
            return probe_route(page, target)
    return {"ok": False, "note": "no usable declaration in the file"}


def probe_question(page: Page, reader: Any) -> dict[str, Any]:
    from personal_os_worker.jobs.browser import registrable_site
    from personal_os_worker.jobs.registry.kinds import learned_question

    for frame in page.frames:
        radios = frame.locator('input[type="radio"]')
        for index in range(radios.count()):
            try:
                text = learned_question(radios.nth(index), registrable_site(page.url))
            except Exception:
                continue
            if text:
                return {"ok": True, "note": f"question read: {text!r}"}
    return {"ok": False, "note": "the reader named no radio group on the page"}


def probe(page: Page, target: dict[str, Any], knowledge: Knowledge,
          declarations: list[Any]) -> dict[str, Any]:
    from personal_os_worker.jobs.safety import ARMABLE_GUARD_SCRIPT, armed

    try:
        session = page.context.new_cdp_session(page)
        identifier: str | None = None
        try:
            session.send("Page.enable")
            # armed() guards the current document. New documents otherwise start
            # disarmed, so routes need a temporary, removable CDP init script.
            identifier = session.send("Page.addScriptToEvaluateOnNewDocument", {
                "source": "window.__personalOsArmed = true;\n" + ARMABLE_GUARD_SCRIPT,
            })["identifier"]
            # Capture modules are scratch code too: guard detection as well.
            with armed(page):
                return probe_candidate(page, target, knowledge, declarations)
        finally:
            try:
                if identifier is not None:
                    session.send("Page.removeScriptToEvaluateOnNewDocument",
                                 {"identifier": identifier})
            finally:
                session.detach()
    except Exception as error:
        return {"ok": False, "note": f"the probe failed: {str(error)[:300]}"}


def main(argv: list[str]) -> int:
    try:
        args = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        from personal_os_worker.jobs import registry

        declarations = registry.load_file(Path(argv[2]), category=argv[3])
        with sync_playwright() as playwright:
            # Attached, never launched: leaving the block disconnects and Chrome runs on.
            browser = playwright.chromium.connect_over_cdp(str(args["cdp_url"]))
            page = find_page(browser, args.get("target_id"), str(args.get("page_url") or ""))
            if page is None:
                result: dict[str, Any] = {"ok": False, "note": "the run's tab is gone"}
            else:
                result = probe(page, dict(args["target"]),
                               knowledge_of(args["knowledge"]), declarations)
    except Exception as error:
        result = {"ok": False, "note": f"the probe crashed: {str(error)[:300]}"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
