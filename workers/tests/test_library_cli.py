"""`os hunt library list|score|put`: the human share and the read-only strip."""

import json
from pathlib import Path
from typing import Any

import pytest

from personal_os_worker.jobs.library import (
    run_library_list,
    run_library_put,
    run_library_score,
)


def entry(code: str = "1.1", **overrides: Any) -> dict[str, Any]:
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "code": code,
        "section": "story",
        "entry_id": None,
        "subject": None,
        "prompt": f"Tell the story for {code}.",
        "guidance": "",
        "answer": "An answer that sounds like me.",
        "display_order": 0,
        "human_percent": None,
        "created_at": "2026-11-01T00:00:00Z",
        "updated_at": "2026-11-01T00:00:00Z",
        **overrides,
    }


class FakeApi:
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries
        self.sent: list[tuple[str, Any]] = []

    def library(self) -> list[dict[str, Any]]:
        return self.entries

    def set_library_human_percent(self, code: str, percent: int) -> dict[str, Any]:
        self.sent.append(("score", (code, percent)))
        row = next(item for item in self.entries if item["code"] == code)
        row["human_percent"] = percent
        return row

    def put_library(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.sent.append(("put_library", entries))
        return entries

    def put_library_entry(self, code: str, item: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(("put_library_entry", (code, item)))
        return item


def test_list_prints_each_band_and_the_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = FakeApi([
        entry("1.1", answer="High human words.", human_percent=92),
        entry("1.2", answer="Medium human words.", human_percent=65),
        entry("1.3", answer="Low human words.", human_percent=30),
        entry("1.4", answer="Unchecked human words.", human_percent=None),
        entry("1.5", answer=None),
    ])

    assert run_library_list(api) == 0

    output = capsys.readouterr().out
    for cell in ("92%", "65%", "30%", "unchecked", "no answer"):
        assert cell in output
    assert "3w" in output and "story" in output and "1.3" in output
    assert output.rstrip().endswith(
        "5 entries · 3 checked · 2 below 80% human · 2 unchecked"
    )


def test_list_truncates_a_long_prompt(capsys: pytest.CaptureFixture[str]) -> None:
    api = FakeApi([entry("4.1", prompt="Prompt " + "x" * 200)])

    assert run_library_list(api) == 0

    output = capsys.readouterr().out
    row = next(line for line in output.splitlines() if line.startswith("4.1"))
    assert row.endswith("…")


def test_score_sets_the_share_and_prints_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    api = FakeApi([entry("2.2.11")])

    assert run_library_score(api, "2.2.11", 88) == 0

    assert api.sent == [("score", ("2.2.11", 88))]
    assert "2.2.11: 88% human" in capsys.readouterr().out


def test_put_strips_read_only_fields(tmp_path: Path) -> None:
    api = FakeApi([])
    file = tmp_path / "library.json"
    file.write_text(json.dumps([entry(), entry("1.2")]), encoding="utf-8")

    assert run_library_put(api, str(file)) == 0

    [(kind, payload)] = api.sent
    assert kind == "put_library"
    for item in payload:
        assert set(item) == {
            "code", "section", "entry_id", "subject", "prompt",
            "guidance", "answer", "display_order",
        }


def test_put_one_entry_strips_read_only_fields(tmp_path: Path) -> None:
    api = FakeApi([])
    file = tmp_path / "entry.json"
    file.write_text(json.dumps(entry("3.4")), encoding="utf-8")

    assert run_library_put(api, str(file)) == 0

    [(kind, (code, payload))] = api.sent
    assert kind == "put_library_entry" and code == "3.4"
    assert "human_percent" not in payload and "id" not in payload
