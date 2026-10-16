"""`os hunt inbox`: everything the system has learned and is waiting on you for.

Three kinds of item, in this order:

- **Candidates** — a binding the model proposed when it filled a question from a
  fact. Accept it and the next application fills that question
  deterministically; reject it and the question comes back unbound.
- **Unbound questions** — met, answered by you, never bound. Bind one to a fact
  (creating the fact from the catalog when it does not exist yet), keep it a
  one-off, or mark it long-form.
- **Prose** — long-form writing from a submitted application, waiting to be
  approved for reuse.

Repaired rules wait here too, but they are validated by watching them fill a
live form, so the Inbox only counts them and points at `os hunt validate`.

Nothing here fills a form; this is where the loop's human step happens. The
archive plays no part: it is for the facts session only (design §0).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

from personal_os_worker.core.api import WorkerApi
from personal_os_worker.core.commands import connected
from personal_os_worker.core.tables import console
from personal_os_worker.jobs.facts.catalog import CATALOG, entry
from personal_os_worker.jobs.facts.commands import VALUE_TYPES, display, parse_value
from personal_os_worker.jobs.facts.derived import career_facts, derive
from personal_os_worker.jobs.facts.values import fact_values

Ask = Callable[[str], str]
FACT_KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class Quit(Exception):
    """The user ended the walk."""


def fact_view(
    facts: list[dict[str, Any]], career: dict[str, Any] | None, today: date
) -> dict[str, Any]:
    """What the system would read for each key: stored over career (R51), then derived."""
    values = {**career_facts(career, today), **fact_values(facts)}
    return {**derive(values, today), **values}


def shown(key: str, values: dict[str, Any]) -> str:
    if key not in values:
        known = entry(key)
        if known is not None and known.source == "derived":
            return "(derived when an application is filled)"
        return "(no fact yet)"
    return display(values[key])


def choose(ask: Ask, prompt: str, options: str) -> str:
    while True:
        answer = ask(prompt).strip().casefold()[:1]
        if answer == "q":
            raise Quit
        if answer and answer in options:
            return answer
        console.print(f"  choose one of: {', '.join(options)}", style="dim")


def print_answers(answers: list[dict[str, Any]]) -> None:
    for answer in answers:
        source = answer.get("source") or "manual"
        state = "submitted" if answer.get("submitted") else "not submitted"
        console.print(
            f"    {answer.get('answer_text')!r}  at {answer.get('company_name')}"
            f"  ({source}, {state})",
            markup=False,
        )


def review_candidate(
    api: WorkerApi,
    item: dict[str, Any],
    *,
    values: dict[str, Any],
    ask: Ask,
    counts: dict[str, int],
) -> None:
    pattern = item["pattern"]
    key = str(pattern["fact_key"])
    console.print(f"\n[bold]Candidate[/bold]  {pattern.get('question_label') or ''}", markup=True)
    console.print(f"  answers from  {key} = {shown(key, values)}", markup=False)
    console.print(f"  proposed by   {pattern.get('origin')}", markup=False)
    if item.get("answers"):
        console.print("  filled", style="dim")
        print_answers(item["answers"])
    choice = choose(ask, "  [a]ccept  [r]eject  [s]kip  [q]uit: ", "ars")
    if choice == "a":
        api.update_pattern(str(pattern["id"]), status="active")
        counts["accepted"] += 1
    elif choice == "r":
        api.update_pattern(str(pattern["id"]), status="rejected")
        counts["rejected"] += 1
    else:
        counts["skipped"] += 1


def ask_fact_key(ask: Ask) -> str:
    """A fact key, with `?` listing the catalog so a key never has to be remembered."""
    while True:
        key = ask("  fact key (? lists them): ").strip()
        if key != "?":
            return key
        for known in CATALOG.values():
            console.print(f"    {known.key:<38} {known.label}", markup=False)


def new_fact(
    api: WorkerApi, key: str, *, last_answer: str | None, ask: Ask
) -> bool:
    """Create the fact a binding needs, from the catalog where it can; False if abandoned."""
    known = entry(key)
    if known is not None and known.source != "stored":
        console.print(f"  {key} is {known.source} and cannot be set here", style="yellow")
        return False
    label = known.label if known else ask("  label for this fact: ").strip()
    value_type = known.value_type if known else ask(f"  type ({', '.join(VALUE_TYPES)}): ").strip()
    if not label or value_type not in VALUE_TYPES:
        console.print("  a new fact needs a label and a type", style="yellow")
        return False
    suggestion = last_answer or ""
    raw = ask(f"  value ({value_type}) [{suggestion}]: ").strip() or suggestion
    try:
        value = parse_value(value_type, raw)
    except ValueError as error:
        console.print(f"  {error}", style="red")
        return False
    api.set_fact(
        key,
        value,
        label=label,
        value_type=value_type,
    )
    console.print(f"  set {key}", style="dim")
    return True



# Words that say nothing about which fact a question wants.
STOPWORDS = frozenset({
    "about", "after", "again", "against", "already", "always", "another", "answer",
    "applying", "because", "been", "before", "being", "below", "between", "could",
    "currently", "does", "doing", "during", "either", "every", "following", "future",
    "have", "here", "however", "includes", "including", "into", "least", "legally",
    "more", "most", "must", "other", "ours", "over", "please", "position", "question",
    "require", "required", "role", "same", "select", "should", "since", "some", "such",
    "than", "that", "their", "them", "then", "there", "these", "they", "this", "those",
    "through", "under", "until", "very", "what", "when", "where", "whether", "which",
    "while", "will", "with", "would", "your", "yours",
})


def propose_expression(label: str) -> str:
    """A regular expression that would match questions like this one.

    The longest word a question does not share with every other question is
    almost always the thing it asks about — "sponsorship", "graduation",
    "clearance". It is a proposal: you type your own over it.
    """
    words = [word for word in re.findall(r"[a-z]{5,}", label.casefold()) if word not in STOPWORDS]
    if not words:
        return ""
    return re.escape(str(max(words, key=len)))


def offer_expression(api: WorkerApi, label: str, key: str, ask: Ask) -> bool:
    """Widen a binding into an expression, which answers questions never met yet.

    A binding answers the one question it names. An expression answers every
    question matching it, which is how one acceptance covers a phrasing no
    application has used yet.
    """
    proposed = propose_expression(label)
    prompt = (
        f"  also answer questions matching a regex? [{proposed}] y, your own, Enter to skip: "
        if proposed
        else "  also answer questions matching a regex? type one, Enter to skip: "
    )
    reply = ask(prompt).strip()
    if not reply:
        return False
    expression = proposed if reply.casefold() == "y" else reply
    if not expression:
        return False
    try:
        re.compile(expression, re.IGNORECASE)
    except re.error as error:
        console.print(f"  that expression does not compile: {error}", style="yellow")
        return False
    api.create_pattern(
        kind="expression",
        expression=expression,
        fact_key=key,
        origin="inbox",
        status="active",
    )
    console.print(f"  /{expression}/ now answers from {key}", style="dim")
    return True


def review_question(
    api: WorkerApi,
    item: dict[str, Any],
    *,
    values: dict[str, Any],
    ask: Ask,
    counts: dict[str, int],
) -> None:
    question = item["question"]
    label = str(question["sample_label"])
    answers = list(item.get("answers") or [])
    console.print(f"\n[bold]Question[/bold]  {label}", markup=True)
    if answers:
        console.print("  you answered", style="dim")
        print_answers(answers)
    choice = choose(ask, "  [b]ind to a fact  [o]ne-off  [l]ong-form  [s]kip  [q]uit: ", "bols")
    if choice == "b":
        key = ask_fact_key(ask)
        if not FACT_KEY.fullmatch(key):
            console.print("  a fact key is dotted lowercase words", style="yellow")
            counts["skipped"] += 1
            return
        if key not in values and not new_fact(
            api, key, last_answer=answers[0]["answer_text"] if answers else None, ask=ask
        ):
            counts["skipped"] += 1
            return
        api.create_pattern(
            kind="binding",
            question_id=str(question["id"]),
            fact_key=key,
            origin="inbox",
            status="active",
        )
        counts["bound"] += 1
        if offer_expression(api, label, key, ask):
            counts["widened"] += 1
    elif choice == "o":
        api.update_question(str(question["id"]), disposition="one_off")
        counts["one_off"] += 1
    elif choice == "l":
        api.update_question(str(question["id"]), disposition="long_form")
        counts["long_form"] += 1
    else:
        counts["skipped"] += 1


def review_prose(
    api: WorkerApi, item: dict[str, Any], *, ask: Ask, counts: dict[str, int]
) -> None:
    text = str(item.get("text") or "")
    console.print(
        f"\n[bold]Prose[/bold]  {item.get('question_label') or ''}"
        f"  [dim](written for {item.get('company_name') or 'an application'})[/dim]"
    )
    console.print("  " + (text if len(text) <= 600 else text[:597] + "..."), markup=False)
    choice = choose(ask, "  [a]pprove for reuse  [d]elete  [s]kip  [q]uit: ", "ads")
    if choice == "a":
        api.update_prose(str(item["id"]), approved=True)
        counts["approved"] += 1
    elif choice == "d":
        api.delete_prose(str(item["id"]))
        counts["deleted"] += 1
    else:
        counts["skipped"] += 1


def walk_inbox(api: WorkerApi, *, ask: Ask = input, today: date | None = None) -> dict[str, int]:
    """Walk every Inbox item; return what was decided."""
    day = today or date.today()
    inbox = api.inbox()
    facts = api.facts()
    try:
        career: dict[str, Any] | None = api.career()
    except RuntimeError:
        career = None
    values = fact_view(facts, career, day)
    expressions = [
        re.compile(str(pattern["expression"]), re.IGNORECASE | re.DOTALL)
        for pattern in api.patterns("active")
        if pattern.get("kind") == "expression" and pattern.get("expression")
    ]
    counts = dict.fromkeys(
        ("accepted", "rejected", "bound", "widened", "one_off", "long_form", "approved",
         "deleted", "skipped"),
        0,
    )
    questions = [
        item
        for item in inbox.get("questions") or []
        if not any(
            expression.search(str(item["question"]["sample_label"])) for expression in expressions
        )
    ]
    repaired = sum(1 for issue in api.autofill_failures() if issue.get("status") == "repaired")
    total = len(inbox.get("candidates") or []) + len(questions) + len(inbox.get("prose") or [])
    if repaired:
        console.print(
            f"{repaired} repaired rule(s) wait for 'os hunt validate', which shows each one "
            "working before it goes live."
        )
    if total == 0:
        console.print("The Inbox is empty." if not repaired else "Nothing else is waiting.")
        return counts
    console.print(f"{total} item(s) waiting.")
    try:
        for candidate in inbox.get("candidates") or []:
            review_candidate(
                api, candidate, values=values, ask=ask, counts=counts
            )
        for question in questions:
            review_question(
                api,
                question,
                values=values,
                ask=ask,
                counts=counts,
            )
        for prose in inbox.get("prose") or []:
            review_prose(api, prose, ask=ask, counts=counts)
    except Quit:
        pass
    return counts


def run_inbox() -> int:
    config, token = connected()
    api = WorkerApi(config, token)
    try:
        counts = walk_inbox(api)
    finally:
        api.close()
    decided = {key: value for key, value in counts.items() if value and key != "skipped"}
    if decided:
        console.print(
            "\n" + ", ".join(f"{value} {key.replace('_', '-')}" for key, value in decided.items())
        )
    return 0
