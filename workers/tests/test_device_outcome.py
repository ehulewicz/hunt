"""What a press did, read from the page after it, and the scorecard line it leaves."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_os_worker.core import wording
from personal_os_worker.device import drive_state, motion, osinput, press
from personal_os_worker.device.drive_state import (
    OUTCOME_LOCKS,
    OUTCOMES,
    Scorecard,
    classify_outcome,
    is_challenge_frame,
)
from personal_os_worker.jobs.browser import SUBMIT_CONTROL

APPLY = "https://jobs.lever.co/pivotal/abc-123/apply"
LEVER_THANKS = "Application submitted! Thank you for applying to Pivotal."
LEVER_REJECTED = (
    "Submit your application Resume/CV ATTACH RESUME/CV Full name Email "
    "There was an error verifying your application. Please try again. Submit application"
)
LEVER_VALIDATION = "Full name ✱ This field is required. Email ✱ Submit application"
NO_CHANGE = "Submit your application Full name Ethan Email me@example.com Submit application"


@pytest.mark.parametrize(
    ("url", "text", "expected"),
    [
        ("https://jobs.lever.co/pivotal/abc-123/thanks", "", "submitted"),
        (APPLY, LEVER_THANKS, "submitted"),
        (APPLY, LEVER_REJECTED, "captcha_rejected"),
        (APPLY, "Error verifying your submission", "captcha_rejected"),
        (APPLY, LEVER_VALIDATION, "validation"),
        (APPLY + "#form", LEVER_VALIDATION, "validation"),
        (APPLY, NO_CHANGE, "unconfirmed"),
        ("https://jobs.lever.co/pivotal/abc-123/elsewhere", LEVER_VALIDATION, "unconfirmed"),
        # A posting's own "is required" is not a field error.
        (APPLY, "A bachelor's degree is required. Submit application", "unconfirmed"),
    ],
)
def test_lever_pages_after_a_press(url: str, text: str, expected: str) -> None:
    assert classify_outcome(url, text, APPLY) == expected


def test_a_rejection_is_never_read_as_gone_in() -> None:
    assert classify_outcome(APPLY.replace("/apply", "/thanks"), LEVER_REJECTED, APPLY) \
        == "captcha_rejected"


def test_a_visible_challenge_and_counted_field_errors() -> None:
    assert classify_outcome(APPLY, NO_CHANGE, APPLY, challenge=True) == "challenge_shown"
    assert classify_outcome(APPLY, NO_CHANGE, APPLY, field_errors=2) == "validation"
    assert classify_outcome(APPLY, LEVER_VALIDATION, APPLY, field_errors=0) == "unconfirmed"


def test_a_challenge_frame_is_visible_and_larger_than_100_square() -> None:
    src = "https://newassets.hcaptcha.com/captcha/v1/abc/static/hcaptcha.html#frame=challenge"
    assert is_challenge_frame(src, 400, 580, True)
    assert not is_challenge_frame(src, 400, 580, False)
    assert not is_challenge_frame(src, 100, 580, True)
    assert not is_challenge_frame(src.replace("challenge", "checkbox"), 400, 580, True)


def test_each_outcome_leaves_its_lock() -> None:
    assert set(OUTCOME_LOCKS) == set(OUTCOMES)
    assert OUTCOME_LOCKS == {
        "submitted": "submitted", "captcha_rejected": "burned", "challenge_shown": "burned",
        "validation": None, "unconfirmed": "unconfirmed",
    }
    assert {state for state in OUTCOME_LOCKS.values() if state} <= drive_state.LOCK_STATES


def test_a_scorecard_line_per_press(tmp_path: Path) -> None:
    card = Scorecard(tmp_path)
    for outcome in ("captcha_rejected", "submitted"):
        card.write(
            listing_id="L1", url=APPLY, experiment=2, knobs={"wait_s": 5.2},
            debugger_attached_at_press=False, input_kind="os", fill_seconds=301.27,
            outcome=outcome, screenshot="/state/drive/shots/L1.jpg",
        )
    lines = [json.loads(line) for line in (tmp_path / "attempts.jsonl").read_text().splitlines()]
    assert [line["outcome"] for line in lines] == ["captcha_rejected", "submitted"]
    first = lines[0]
    assert first["ats"] == "jobs.lever.co" and first["experiment"] == 2
    assert first["knobs"] == {"wait_s": 5.2} and first["input_kind"] == "os"
    assert first["debugger_attached_at_press"] is False and first["fill_seconds"] == 301.3
    assert first["email_confirmed"] is None and first["screenshot"].endswith("L1.jpg")
    assert first["listing_id"] == "L1" and first["url"] == APPLY and first["ts"]


@pytest.mark.parametrize(("outcome", "kind"), [("gone", "os"), ("submitted", "mouse")])
def test_a_scorecard_refuses_an_unknown_outcome_or_input(
    tmp_path: Path, outcome: str, kind: str
) -> None:
    with pytest.raises(ValueError):
        Scorecard(tmp_path).write(
            listing_id="L1", url=APPLY, experiment=1, knobs={},
            debugger_attached_at_press=True, input_kind=kind, fill_seconds=1,
            outcome=outcome, screenshot=None,
        )
    assert not (tmp_path / "attempts.jsonl").exists()


def test_the_press_words_are_shared_and_the_device_never_imports_jobs() -> None:
    assert SUBMIT_CONTROL is wording.SUBMIT_CONTROL
    assert wording.CAPTCHA_REJECTED.search("There was an error verifying your application.")
    for module in (drive_state, motion, osinput, press):
        assert "personal_os_worker.jobs" not in Path(module.__file__ or "").read_text()


def test_greenhouse_and_ashby_confirmations_read_as_submitted() -> None:
    gh = "https://job-boards.greenhouse.io/acme/jobs/123"
    assert classify_outcome(gh + "/confirmation", "Thank you for applying.", gh) == "submitted"
    ashby = "https://jobs.ashbyhq.com/acme/0b9c/application"
    said = "Your application was successfully submitted. We'll contact you if there are next steps."
    assert classify_outcome(ashby, said, ashby) == "submitted"


def test_a_recaptcha_challenge_frame_counts() -> None:
    src = "https://www.google.com/recaptcha/enterprise/bframe?hl=en&k=abc"
    assert is_challenge_frame(src, 400, 580, True)
    assert not is_challenge_frame(src.replace("bframe", "anchor"), 400, 580, True)
