"""The cover-letter review: Hunt page when linked, left for you otherwise."""

from __future__ import annotations

from pathlib import Path

from personal_os_worker.jobs.cover_letter import PLACEHOLDERS, review_letter


class FakeLink:
    def __init__(self, answered=True, replacement="The page's letter.\n"):
        self.answered = answered
        self.replacement = replacement
        self.sent = None

    def edit_letter(self, path: Path) -> bool:
        self.sent = path.read_text(encoding="utf-8")
        if not self.answered:
            return False
        path.write_text(self.replacement, encoding="utf-8")
        return True


LETTER = "Dear Hiring Team,\n\nA filled letter.\n"
WITH_PROMPT = LETTER + PLACEHOLDERS["hook"] + "\n"


def test_accept_keeps_a_fully_filled_letter(tmp_path):
    path = tmp_path / "cover_letter.md"
    path.write_text(LETTER, encoding="utf-8")
    review_letter(path, link=None, accept=True)
    assert path.read_text(encoding="utf-8") == LETTER


def test_a_letter_with_prompts_is_never_auto_accepted(tmp_path):
    path = tmp_path / "cover_letter.md"
    path.write_text(WITH_PROMPT, encoding="utf-8")
    review_letter(path, link=None, accept=True)
    assert path.read_text(encoding="utf-8") == ""


def test_a_linked_run_sends_the_letter_to_the_page_and_keeps_its_answer(tmp_path):
    path = tmp_path / "cover_letter.md"
    path.write_text(LETTER, encoding="utf-8")
    link = FakeLink(answered=True)
    review_letter(path, link=link, accept=False)
    assert link.sent == LETTER
    assert path.read_text(encoding="utf-8") == "The page's letter.\n"


def test_an_agent_that_went_away_leaves_the_letter_for_you(tmp_path):
    path = tmp_path / "cover_letter.md"
    path.write_text(LETTER, encoding="utf-8")
    review_letter(path, link=FakeLink(answered=False), accept=False)
    assert path.read_text(encoding="utf-8") == ""


def test_no_link_and_no_accept_empties_the_file(tmp_path):
    path = tmp_path / "cover_letter.md"
    path.write_text(LETTER, encoding="utf-8")
    review_letter(path, link=None, accept=False)
    assert path.read_text(encoding="utf-8") == ""
