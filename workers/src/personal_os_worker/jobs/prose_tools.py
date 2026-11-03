"""`os hunt prose try` and `os hunt prose coverage`: the composer over a batch.

Neither fills a field nor writes prose. `try` composes every prose question in
the audit TSV and writes a report; `coverage` lists open gaps, or scans stored
prose questions and records/resolves gaps. Model calls go through
jobs/compose.py with purpose "draft", each question its own begin_run.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personal_os_worker.core.tables import console
from personal_os_worker.jobs import model
from personal_os_worker.jobs.compose import LibraryText, Posting, compose
from personal_os_worker.jobs.library import link_entries, parse_bank
from personal_os_worker.jobs.resolve import Knowledge

if TYPE_CHECKING:
    from personal_os_worker.core.api import WorkerApi

SKIP_CLUSTERS = {"fact", "followup", "quiz"}
FACT_HEADINGS = ("org", "employer", "title")


def _linked_names(
    entries: Sequence[dict[str, Any]], career: dict[str, Any]
) -> dict[str, str]:
    """Each entry's linked career entry heading (`title | org`, or just title)."""
    career_rows = {
        str(row.get("id")): row
        for section in ("experience", "research", "projects", "activities")
        for row in (career.get(section) or [])
    }
    names = {}
    for entry in entries:
        row = career_rows.get(str(entry.get("entry_id") or ""))
        if row:
            parts = [str(row.get("title") or ""), str(row.get("org") or "")]
            names[entry["code"]] = " | ".join(part for part in parts if part)
    return names


def library_texts(entries: Sequence[dict[str, Any]], career: dict[str, Any]) -> list[LibraryText]:
    """Answered entries only, as the composer sees them."""
    names = _linked_names(entries, career)
    return [
        LibraryText(
            code=entry["code"], section=entry["section"],
            subject=entry.get("subject"), linked=names.get(entry["code"]),
            prompt=entry.get("prompt") or "", answer=entry.get("answer") or "",
        )
        for entry in entries
        if (entry.get("answer") or "").strip()
    ]


def _facts(api: WorkerApi, knowledge: Knowledge) -> list[tuple[str, str, Any]]:
    return [
        (key, str(knowledge.labels.get(key) or key), value)
        for key, value in knowledge.facts.items()
    ]


def run_prose_try(
    api: WorkerApi, *, library: Path, questions: Path, out: Path, max_calls: int | None
) -> int:
    career = api.career().get("document") or {}
    entries, _warnings = link_entries(parse_bank(library), career)
    texts = library_texts(entries, career)
    knowledge = Knowledge.build(facts=api.facts(), career=api.career())
    facts = _facts(api, knowledge)
    rows = [
        row for row in csv.DictReader(questions.open(encoding="utf-8"), delimiter="\t")
        if row.get("cluster") not in SKIP_CLUSTERS
    ]
    board = api.listings(limit=1000).get("items") or []
    postings = {
        (str(item.get("company_name") or "").casefold(), str(item.get("title") or "").casefold()):
            str(item.get("description") or "")
        for item in board
    }
    budget = max_calls if max_calls is not None else 2 * len(rows)
    results: list[dict[str, Any]] = []
    calls = 0
    try:
        for row in rows:
            if calls >= budget:
                results.append({"row": row, "not_run": True})
                continue
            model.begin_run()
            posting = Posting(
                company=row.get("company") or "", title=row.get("title") or "",
                text=postings.get(
                    ((row.get("company") or "").casefold(),
                     (row.get("title") or "").casefold()),
                    "",
                ),
            )
            result = compose(
                question=row.get("question") or "", max_length=None,
                posting=posting, facts=facts, career=career, library=texts,
                attempts=2,
            )
            calls += result.attempts
            results.append({"row": row, "result": result})
    finally:
        model.end_run()
    report = _try_report(results)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    console.print(_try_totals(results))
    return 0


def _try_totals(results: list[dict[str, Any]]) -> str:
    run = [item for item in results if "result" in item]
    counts = {"full": 0, "composable": 0, "partial": 0, "missing": 0, "failed": 0}
    calls = 0
    guard_failures: dict[str, int] = {}
    for item in run:
        result = item["result"]
        calls += result.attempts
        if result.composition is None:
            counts["failed"] += 1
        else:
            counts[result.composition.coverage] += 1
        for failure in result.failures:
            guard_failures[failure] = guard_failures.get(failure, 0) + 1
    not_run = len(results) - len(run)
    lines = [
        f"rows run: {len(run)}",
        f"coverage: full {counts['full']}, composable {counts['composable']}, "
        f"partial {counts['partial']}, missing {counts['missing']}, "
        f"failed guards {counts['failed']}",
        f"model calls used: {calls}",
    ]
    if not_run:
        lines.append(f"not run: {not_run} (--max-calls reached)")
    for failure, count in sorted(guard_failures.items()):
        lines.append(f"guard failure: {failure} ({count})")
    return "\n".join(lines)


def _try_report(results: list[dict[str, Any]]) -> str:
    lines = ["# Prose try", "", "## Totals", "", _try_totals(results), ""]
    for item in results:
        row = item["row"]
        lines.append(f"## {row.get('company')} — {row.get('title')}")
        lines.append("")
        lines.append(f"Question: {row.get('question')}")
        lines.append(f"Audit family (TSV): {row.get('family')}")
        if "result" not in item:
            lines.append("not run: --max-calls reached")
            lines.append("")
            continue
        result = item["result"]
        lines.append(f"attempts: {result.attempts}")
        if result.composition is not None:
            composed = result.composition
            lines.append(f"composer family: {composed.family}")
            lines.append(f"coverage: {composed.coverage}")
            lines.append(f"sources: {', '.join(composed.sources) or '-'}")
            lines.append(f"interest: {composed.interest}")
            lines.append(f"missing detail: {composed.missing_detail or '-'}")
            lines.append("")
            lines.append(composed.answer or "(empty)")
        else:
            lines.append("failed guards: " + "; ".join(result.failures))
        lines.append("")
    return "\n".join(lines)


def run_prose_coverage(
    api: WorkerApi, *, scan: bool, max_calls: int | None
) -> int:
    if not scan:
        _print_open_gaps(api)
        return 0

    from personal_os_worker.jobs.autofill import is_factual_box, is_long_form_field

    career = api.career().get("document") or {}
    entries = api.library()
    texts = library_texts(entries, career)
    knowledge = Knowledge.build(facts=api.facts(), career=api.career())
    facts = _facts(api, knowledge)
    open_gaps = {
        str(gap.get("question_id")): gap for gap in api.prose_gaps(open_only=True)
    }
    prose_rows = api.prose()
    newest_company, newest_title, newest_application = _newest_applications(prose_rows)

    questions = [
        item for item in api.questions()
        if (item.get("form") == "long" or item.get("disposition") == "long_form")
        and is_long_form_field({"label": item.get("sample_label") or "",
                                "tag": "textarea", "type": "textarea"})
        and not is_factual_box({"label": item.get("sample_label") or "",
                                "tag": "textarea", "type": "textarea"})
    ]
    budget = max_calls if max_calls is not None else 2 * len(questions)
    recorded = resolved = failed = not_run = 0
    calls = 0
    try:
        for item in questions:
            if calls >= budget:
                not_run += 1
                continue
            question_id = str(item.get("id"))
            label = str(item.get("sample_label") or "")
            model.begin_run()
            result = compose(
                question=label, max_length=None,
                posting=Posting(company=newest_company.get(question_id, ""),
                                title=newest_title.get(question_id, ""), text=""),
                facts=facts, career=career, library=texts,
                classify_only=True, attempts=2,
            )
            calls += result.attempts
            if result.composition is None:
                failed += 1
                continue
            composed = result.composition
            if composed.coverage in ("partial", "missing"):
                api.create_prose_gap({
                    "question": label,
                    "application_id": newest_application.get(question_id) or None,
                    "family": composed.family, "coverage": composed.coverage,
                    "missing_detail": composed.missing_detail,
                })
                recorded += 1
            else:
                open_gap = open_gaps.get(question_id)
                if open_gap is not None:
                    api.resolve_prose_gap(str(open_gap["id"]))
                    resolved += 1
    finally:
        model.end_run()
    console.print(
        f"recorded {recorded}, resolved {resolved}, failed {failed}, not run {not_run}"
    )
    return 0


def _print_open_gaps(api: WorkerApi) -> None:
    """The open gaps by family, the families most companies share first."""
    gaps = api.prose_gaps(open_only=True)
    families: dict[str, list[dict[str, Any]]] = {}
    for gap in gaps:
        families.setdefault(str(gap.get("family") or "other"), []).append(gap)

    def rank(family_gaps: list[dict[str, Any]]) -> tuple[int, int]:
        companies = {str(g.get("company_name") or "") or "no application"
                     for g in family_gaps}
        return (-len(companies), -len(family_gaps))

    for family in sorted(families, key=lambda f: rank(families[f])):
        group = families[family]
        by_coverage: dict[str, int] = {}
        companies = sorted({str(g.get("company_name") or "") or "no application"
                            for g in group})
        details = sorted({str(g.get("missing_detail") or "")
                          for g in group if g.get("missing_detail")})
        for gap in group:
            coverage = str(gap.get("coverage") or "?")
            by_coverage[coverage] = by_coverage.get(coverage, 0) + 1
        counts = ", ".join(f"{key} {value}" for key, value in sorted(by_coverage.items()))
        console.print(f"[bold]{family}[/bold] — {counts}")
        console.print(f"  companies: {', '.join(companies)}")
        for gap in group[:3]:
            console.print(f"  - {gap.get('question')}")
        if details:
            console.print(f"  missing: {'; '.join(details)}")
    if not families:
        console.print("No open gaps.")


def _newest_applications(
    prose_rows: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Each question's company, title and application id, from its first prose row."""
    newest_company: dict[str, str] = {}
    newest_title: dict[str, str] = {}
    newest_application: dict[str, str] = {}
    for row in prose_rows:
        question_id = str(row.get("question_id"))
        if question_id not in newest_company:
            newest_company[question_id] = str(row.get("company_name") or "")
            newest_title[question_id] = str(row.get("title") or "")
            newest_application[question_id] = str(row.get("application_id") or "")
    return newest_company, newest_title, newest_application
