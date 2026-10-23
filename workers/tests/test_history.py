from typing import Any, ClassVar

import pytest

from personal_os_worker.cli import build_parser, main
from personal_os_worker.core.tables import console
from personal_os_worker.jobs import history

CODE_ID = "7a1b2c3d-0000-4000-8000-000000000001"
FACT_ID = "9f8e7d6c-0000-4000-8000-000000000002"
RUN_ID = "11112222-3333-4444-5555-666677778888"
ISSUE_ID = "aaaabbbb-cccc-4ddd-8eee-ffff00001111"
DIFF = "diff --git a/x.py b/x.py\n-old = 1\n+new = 2\n"

CODE_CHANGE = {
    "id": CODE_ID, "subject": "code", "subject_id": "3f8ea0f1234567", "action": "create",
    "before": {"commit": "870e7b5abcdef0"},
    "after": {"commit": "3f8ea0f1234567", "summary": "Read the combobox", "diff": DIFF},
    "origin": "worker", "run_id": RUN_ID, "question_key": "how did you hear about us",
    "failure_issue_id": ISSUE_ID, "status": "passed", "sampled": 12, "failed": 0,
    "created_at": "2026-10-21T15:00:00+00:00", "verified_at": "2026-10-21T15:01:00+00:00",
}
FACT_CHANGE = {
    "id": FACT_ID, "subject": "fact", "subject_id": "education.gpa", "action": "update",
    "before": {"key": "education.gpa", "value": "3.7", "updated_at": "a"},
    "after": {"key": "education.gpa", "value": "3.78", "updated_at": "b"},
    "origin": "you", "run_id": None, "question_key": None, "failure_issue_id": None,
    "status": None, "sampled": None, "failed": None,
    "created_at": "2026-10-20T15:00:00+00:00", "verified_at": None,
}


class FakeApi:
    calls: ClassVar[list[tuple[str, Any]]] = []

    def __init__(self, config: object, token: str) -> None:
        return None

    def changes(self, **filters: Any) -> list[dict[str, Any]]:
        FakeApi.calls.append(("changes", filters))
        return [CODE_CHANGE, FACT_CHANGE][: int(filters["limit"])]

    def autofill_failures(self) -> list[dict[str, Any]]:
        return [
            {"id": ISSUE_ID, "status": "unsolved", "category": "widget",
             "field_label": "Start date", "occurrence_count": 3, "fixed_commit": None,
             "verification_notes": "three attempts, the picker never read back"},
            {"id": "bbbbcccc-0000-4000-8000-000000000003", "status": "fixed",
             "category": "strategy", "field_label": "Country", "occurrence_count": 1,
             "fixed_commit": "3f8ea0f1234567", "verification_notes": "attempt 1: read back"},
            {"id": "ccccdddd-0000-4000-8000-000000000004", "status": "open",
             "category": "format", "field_label": "Salary", "occurrence_count": 2},
        ]

    def autofill_sites(self) -> list[dict[str, Any]]:
        return [
            {"site": "ashbyhq.com", "runs": 4, "run_id": RUN_ID, "run_status": "review_ready",
             "model_calls": 0, "last_run_at": "2026-10-21T15:00:00+00:00",
             "open_issues": 0, "unsolved_issues": 0},
            {"site": "myworkdayjobs.com", "runs": 2, "run_id": RUN_ID, "run_status": "running",
             "model_calls": 5, "last_run_at": "2026-10-20T15:00:00+00:00",
             "open_issues": 2, "unsolved_issues": 1},
        ]

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def fake_api(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeApi.calls = []
    monkeypatch.setattr(history, "connected", lambda: ("config", "token"))
    monkeypatch.setattr(history, "WorkerApi", FakeApi)
    monkeypatch.setattr(console, "width", 240)


def test_changes_lists_who_what_it_fixed_and_its_check(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["hunt", "changes", "--subject", "code", "--status", "passed", "5"]) == 0

    assert FakeApi.calls == [("changes", {"limit": 5, "subject": "code", "status": "passed"})]
    output = capsys.readouterr().out
    assert "code 3f8ea0f" in output
    assert "worker" in output
    assert "run 11112222 issue aaaabbbb how did you hear" in output
    assert "passed 0/12" in output
    assert "870e7b5 → 3f8ea0f Read the combobox" in output
    assert "fact education.gpa" in output
    assert "value 3.7 → 3.78" in output, "only the fields that differ"
    assert "updated_at" not in output


def test_changes_defaults_to_twenty_rows_and_rejects_a_stray_word() -> None:
    assert main(["hunt", "changes"]) == 0
    assert FakeApi.calls == [("changes", {"limit": 20})]
    with pytest.raises(SystemExit, match="number of rows or 'show'"):
        main(["hunt", "changes", "everything"])
    with pytest.raises(SystemExit, match="needs a row"):
        main(["hunt", "changes", "show"])
    with pytest.raises(SystemExit) as refused:
        build_parser().parse_args(["hunt", "changes", "--subject", "listing"])
    assert refused.value.code == 2


def test_changes_show_prints_one_by_row_or_prefix_with_its_diff(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["hunt", "changes", "show", "1"]) == 0
    output = capsys.readouterr().out
    assert FakeApi.calls[0][1]["limit"] == history.MAX_CHANGES
    assert CODE_ID in output
    assert "Read the combobox" in output
    assert "+new = 2" in output
    assert output.count("+new = 2") == 1, "the diff prints once, not inside after"

    assert main(["hunt", "changes", "show", "9f8e7d"]) == 0
    output = capsys.readouterr().out
    assert "education.gpa" in output and "3.78" in output
    with pytest.raises(SystemExit, match="No changes match"):
        main(["hunt", "changes", "show", "deadbeef"])


def test_fixes_lists_only_what_the_fixer_took_on(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["hunt", "fixes"]) == 0
    output = capsys.readouterr().out
    assert "Fixes (2)" in output
    assert "Start date" in output and "the picker never read back" in output
    assert "3f8ea0f" in output and "x3" in output
    assert "Salary" not in output, "an open failure is not the fixer's yet"


def test_sites_marks_a_site_whose_latest_run_needed_no_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["hunt", "sites"]) == 0
    output = capsys.readouterr().out
    assert "Sites (1 of 2 need no learning or fixing)" in output
    ashby = next(line for line in output.splitlines() if "ashbyhq.com" in line)
    workday = next(line for line in output.splitlines() if "myworkdayjobs.com" in line)
    assert "met" in ashby
    assert "met" not in workday and "(running)" in workday
