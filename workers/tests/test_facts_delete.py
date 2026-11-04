"""Deleting a stored fact is explicit; record and computed facts have a different owner."""

from __future__ import annotations

from typing import Any

import pytest

from personal_os_worker.cli import build_parser, main
from personal_os_worker.jobs.facts import commands


class Api:
    def __init__(self, *args: Any) -> None:
        self.rows = [{"key": "education.gpa", "value": 3.78}]
        self.deleted: list[str] = []
        self.closed = False

    def facts(self) -> list[dict[str, Any]]:
        return self.rows

    def delete_fact(self, key: str) -> None:
        self.deleted.append(key)

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("reply, expected", [("yes", True), ("Y", True), ("no", False),
                                             ("", False)])
def test_delete_asks_before_removing_a_stored_override(reply: str, expected: bool) -> None:
    api = Api()
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return reply

    assert commands.delete_fact(api, "education.gpa", ask=ask) is expected
    assert api.deleted == (["education.gpa"] if expected else [])
    assert len(prompts) == 1 and "education.gpa" in prompts[0]


@pytest.mark.parametrize("key, explanation", [
    ("education.school_name", "os hunt resume put"),
    ("identity.first_name", "set identity.legal_name"),
    ("availability.earliest_start_date", "set availability.notice_period_weeks"),
    ("unknown.fact", "No stored fact"),
])
def test_nonstored_keys_are_refused_even_with_yes(key: str, explanation: str) -> None:
    api = Api()
    with pytest.raises(RuntimeError, match=explanation):
        commands.delete_fact(api, key, yes=True, ask=lambda _: pytest.fail("must not ask"))
    assert api.deleted == []


def test_delete_parser_and_dispatch_use_the_existing_api(monkeypatch: pytest.MonkeyPatch) -> None:
    args = build_parser().parse_args(["hunt", "facts", "delete", "education.gpa"])
    assert args.key == "education.gpa" and not args.yes
    api = Api()
    monkeypatch.setattr(commands, "connected", lambda: (None, None))
    monkeypatch.setattr(commands, "WorkerApi", lambda *args: api)
    assert main(["hunt", "facts", "delete", "education.gpa", "--yes"]) == 0
    assert api.deleted == ["education.gpa"] and api.closed


def test_delete_closes_the_api_when_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    api = Api()
    monkeypatch.setattr(commands, "connected", lambda: (None, None))
    monkeypatch.setattr(commands, "WorkerApi", lambda *args: api)
    with pytest.raises(RuntimeError, match="os hunt resume put"):
        commands.run_facts_delete("education.school_name", yes=True)
    assert api.closed and not api.deleted
