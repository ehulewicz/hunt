"""`os hunt facts`: list, review, set, and delete what is true about you.

Review walks every unverified fact, shows what the archived answers said to
similar questions beside it, and records your decision on the row itself.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from typing import Any

from rich import box
from rich.markup import escape
from rich.table import Table

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.commands import connected
from personal_os_worker.core.config import console
from personal_os_worker.jobs.facts.catalog import entry
from personal_os_worker.jobs.facts.derived import (
    CAREER_FACTS,
    DERIVED_FACTS,
    READ_ONLY_FACTS,
    career_facts,
    derive,
    read_only_refusal,
    stored_values,
)

VALUE_TYPES = ("text", "boolean", "number", "date", "choice", "list", "object")

# Archived questions that restated a fact, so review can show what they said.
LIBRARY_HINTS = {
    "identity.legal_name": r"\b(first|last|full|legal|given|family) name\b|\bsurname\b",
    "identity.preferred_name": r"\bpreferred (first )?name\b",
    "contact.email": r"\be-?mail\b",
    "contact.phone": r"\bphone\b",
    "contact.linkedin_url": r"\blinked\s*in\b",
    "contact.github_url": r"\bgit\s*hub\b",
    "address.city": r"\bcity\b|\bcurrently (located|based)\b",
    "work_auth.us_citizen": r"\bcitizen",
    "work_auth.authorized_us": r"\bauthori[sz]",
    "work_auth.requires_sponsorship": r"\bsponsor",
    "education.school_name": r"\b(school|university|college)\b",
    "education.degree_type": r"\bdegree\b",
    "education.field_of_study": r"\bmajor\b|\bfield of study\b",
    "education.graduation_date": r"\bgraduat",
    "education.gpa": r"\bgpa\b|grade point",
    "availability.notice_period_weeks": r"\bnotice\b|\bstart date\b",
    "experience.years_professional": r"\byears of\b.*\bexperience\b",
    "preferences.willing_to_relocate": r"\brelocat",
    "preferences.willing_to_travel": r"\btravel\b",
    "compensation.minimum": r"\bsalary\b|\bcompensation\b|\bpay\b|\bhourly\b",
    "eeo.gender_identity": r"\bgender\b|\btransgender\b",
    "eeo.race_ethnicities": r"\brace\b|\bethnic|\bhispanic|\blatin",
    "eeo.veteran_status": r"\bveteran\b",
    "eeo.disability_status": r"\bdisabilit",
}


def none_value(value_type: str) -> Any:
    """The empty value of a type: a fact that says the answer is none (blank on purpose)."""
    if value_type == "text":
        return ""
    if value_type == "list":
        return []
    raise ValueError(f"A {value_type} fact cannot be none; only text and list facts can")


def parse_value(value_type: str, raw: str) -> Any:
    """A typed value from what was typed at the prompt; ValueError if it does not fit."""
    text = raw.strip()
    if value_type in {"text", "choice"}:
        if not text:
            raise ValueError("Enter a value, or delete the fact instead")
        return text
    if value_type == "boolean":
        lowered = text.casefold()
        if lowered in {"y", "yes", "true", "t"}:
            return True
        if lowered in {"n", "no", "false", "f"}:
            return False
        raise ValueError("Enter yes or no")
    if value_type == "number":
        try:
            number = float(text)
        except ValueError:
            raise ValueError("Enter a number") from None
        return int(number) if number.is_integer() else number
    if value_type == "date":
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            raise ValueError("Enter a date as YYYY-MM-DD") from None
    if value_type in {"list", "object"}:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            shape = "array" if value_type == "list" else "object"
            raise ValueError(f"Enter a JSON {shape}") from None
        if value_type == "list" and not isinstance(parsed, list):
            raise ValueError("Enter a JSON array")
        if value_type == "object" and not isinstance(parsed, dict):
            raise ValueError("Enter a JSON object")
        return parsed
    raise ValueError(f"Unknown fact type {value_type!r}")


def display(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list | dict):
        return json.dumps(value)
    return str(value)


def library_conflicts(key: str, answers: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Distinct archived answers to questions that look like they restate this fact."""
    hint = LIBRARY_HINTS.get(key)
    if hint is None:
        return []
    pattern = re.compile(hint, re.IGNORECASE)
    seen: dict[str, str] = {}
    for item in answers:
        question = str(item.get("question") or item.get("normalized_question") or "")
        answer = str(item.get("answer_text") or "").strip()
        if pattern.search(question) and answer and answer not in seen:
            seen[answer] = question
    return [(question, answer) for answer, question in seen.items()]


def render_facts(
    facts: list[dict[str, Any]],
    *,
    today: date,
    career: dict[str, Any] | None = None,
) -> None:
    stored = stored_values(facts)
    from_career = career_facts(career, today)
    derived = derive({**from_career, **stored}, today)
    read_only = {**from_career, **derived}
    table = Table(
        title=(
            f"Facts ({len(facts)} stored, {len(DERIVED_FACTS)} derived, "
            f"{len(from_career)} from the career record)"
        ),
        header_style="bold",
        box=box.SIMPLE_HEAD,
        padding=(0, 1),
        expand=True,
    )
    table.add_column("key", no_wrap=True)
    table.add_column("value", overflow="fold", ratio=2)
    table.add_column("type", no_wrap=True, style="dim")
    table.add_column("state", no_wrap=True)
    for fact in facts:
        key = str(fact["key"])
        state = "verified" if fact.get("verified_at") else "[yellow]unverified[/yellow]"
        found = READ_ONLY_FACTS.get(key)
        if found is not None:
            static = found[1].kind == "static"
            value = read_only.get(key)
            shown = (
                escape(display(value)) if value is not None
                else "not in the record" if static else "missing inputs"
            )
            state += (
                f" · [magenta]ignored, {'from the career record' if static else 'computed'}: "
                f"{shown}[/magenta]"
            )
        table.add_row(
            key,
            display(fact.get("value")),
            str(fact.get("value_type")),
            state,
        )
    for item in CAREER_FACTS:
        if item.key not in from_career:
            continue
        table.add_row(
            f"[cyan]{item.key}[/cyan]", escape(display(from_career[item.key])), "career", ""
        )
    for item in DERIVED_FACTS:
        value = derived.get(item.key)
        table.add_row(
            f"[cyan]{item.key}[/cyan]",
            display(value) if value is not None else "[dim]missing inputs[/dim]",
            "derived",
            "",
        )
    console.print(table)
    ignored = sum(1 for fact in facts if str(fact["key"]) in READ_ONLY_FACTS)
    if ignored:
        console.print(
            f"{ignored} stored row(s) sit under record or computed keys and are ignored; "
            "os hunt facts delete KEY removes one.",
            style="dim",
        )


def run_facts_list() -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        facts = api.facts()
        try:
            career: dict[str, Any] | None = api.career()
        except RuntimeError:
            career = None
    finally:
        api.close()
    render_facts(facts, today=date.today(), career=career)
    return 0


def delete_fact(
    api: WorkerApi, key: str, *, yes: bool = False, ask: Callable[[str], str] = input,
) -> bool:
    """Delete a stored row; a row under a read-only key is a leftover the worker ignores."""
    stored = next((fact for fact in api.facts() if fact["key"] == key), None)
    if stored is None:
        refusal = read_only_refusal(key)
        if refusal is not None:
            raise RuntimeError(refusal)
        raise RuntimeError(f"No stored fact named {key}")
    console.print(f"{key} = {display(stored.get('value'))}", markup=False)
    if not yes and ask(f"Delete stored fact {key}? [y/N] ").strip().casefold() not in {"y", "yes"}:
        console.print("Cancelled.")
        return False
    api.delete_fact(key)
    console.print(f"Deleted {key}.", markup=False)
    return True


def run_facts_delete(key: str, *, yes: bool = False) -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        delete_fact(api, key, yes=yes)
    finally:
        api.close()
    return 0


def review_facts(
    api: WorkerApi, *, ask: Callable[[str], str] = input
) -> tuple[int, int]:
    """Walk unverified facts; return (decided, remaining)."""
    facts = [fact for fact in api.facts() if not fact.get("verified_at")]
    answers = api.archive_answers()
    decided = 0
    for position, fact in enumerate(facts, start=1):
        key = str(fact["key"])
        console.print(
            f"\n[bold]{position}/{len(facts)}  {key}[/bold]  [dim]{fact.get('label')}"
            f" · {fact.get('value_type')}[/dim]"
        )
        console.print(f"  value    {display(fact.get('value'))}", markup=False)
        conflicts = library_conflicts(key, answers)
        if conflicts:
            console.print("  archived answers to similar questions:", style="dim")
            for question, answer in conflicts[:8]:
                console.print(f"    {answer!r:<40}  ← {question[:70]}", markup=False)
        refusal = read_only_refusal(key)
        if refusal is not None:
            console.print(f"  {refusal}", style="dim")
        while True:
            choice = ask(
                "  [d]elete  [s]kip  [q]uit: "
                if refusal is not None
                else "  [a]ccept  [e]dit  [d]elete  [s]kip  [q]uit: "
            ).strip().casefold()
            if choice in {"a", "accept"} and refusal is None:
                api.verify_fact(key)
                decided += 1
                break
            if choice in {"e", "edit"} and refusal is None:
                raw = ask(f"  new value ({fact.get('value_type')}): ")
                try:
                    value = parse_value(str(fact.get("value_type")), raw)
                except ValueError as error:
                    console.print(f"  {error}", style="red")
                    continue
                from personal_os_worker.jobs.knowledge import fact_replay, report_fact_replay

                check = fact_replay(api, key, value)
                api.set_fact(key, value, verified=True,
                             verification=check.verification() if check else None)
                report_fact_replay(key, check)
                decided += 1
                break
            if choice in {"d", "delete"}:
                api.delete_fact(key)
                decided += 1
                break
            if choice in {"s", "skip"}:
                break
            if choice in {"q", "quit"}:
                return decided, len(facts) - decided
    return decided, len(facts) - decided


def run_facts_review() -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        decided, remaining = review_facts(api)
    finally:
        api.close()
    console.print(f"\n{decided} decided, {remaining} still unverified.")
    return 0


def run_facts_set(
    key: str,
    raw: str | None,
    *,
    value_type: str | None,
    label: str | None,
) -> int:
    """Set one fact; `raw` None stores the type's none value. The catalog names a new
    fact's type and label unless they are given."""
    refusal = read_only_refusal(key)
    if refusal is not None:
        raise RuntimeError(refusal)
    config, token = connected()
    api = WorkerApi(config, token)
    known = entry(key)
    try:
        existing = {str(fact["key"]): fact for fact in api.facts()}.get(key)
        resolved_type = (
            value_type
            or (existing or {}).get("value_type")
            or (known.value_type if known else None)
        )
        if resolved_type is None:
            raise RuntimeError(f"{key} is a new fact: give --type and --label")
        if existing is None and not label:
            if known is None:
                raise RuntimeError(f"{key} is a new fact: give --label")
            label = known.label
        value = (
            none_value(str(resolved_type)) if raw is None else parse_value(str(resolved_type), raw)
        )
        from personal_os_worker.jobs.knowledge import fact_replay, report_fact_replay

        check = fact_replay(api, key, value, value_type=str(resolved_type))
        fact = api.set_fact(
            key,
            value,
            label=label,
            value_type=value_type or (None if existing else str(resolved_type)),
            verification=check.verification() if check else None,
        )
        report_fact_replay(key, check)
    finally:
        api.close()
    console.print(f"{fact['key']} = {display(fact['value'])}  (verified)", markup=False)
    return 0
