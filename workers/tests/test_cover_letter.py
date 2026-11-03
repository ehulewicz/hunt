"""The cover letter: template, slots, and the API-compiled PDF."""

from __future__ import annotations

from personal_os_worker.jobs.cover_letter import (
    letter_pdf,
    load_template,
    placeholder_slots,
    render_letter,
)


class FakeApi:
    def __init__(self):
        self.calls = []

    def cover_letter_pdf(self, text, *, company, date):
        self.calls.append({"text": text, "company": company, "date": date})
        return b"%PDF-letter"


def test_the_template_and_placeholder_slots_render():
    template = load_template()
    slots = placeholder_slots(template)
    text = render_letter(template, slots, company="Acme", role="Intern")
    assert "Acme" in text and "[hook:" in text


def test_letter_pdf_asks_the_api_and_names_the_file(tmp_path):
    api = FakeApi()
    contact = {"name": "Alex Example"}
    path = letter_pdf(api, contact, "Dear team,\n\nHi.", tmp_path, company="Acme")
    assert path == tmp_path / "Alex_Example_Cover_Letter.pdf"
    assert path.read_bytes() == b"%PDF-letter"
    assert api.calls[0]["text"] == "Dear team,\n\nHi."
    assert api.calls[0]["company"] == "Acme"
    assert api.calls[0]["date"]


def test_letter_prompt_carries_the_style_rules():
    from personal_os_worker.jobs.cover_letter import LetterTemplate, _prompt

    prompt = _prompt(
        LetterTemplate(letter="Dear X,", modules={"a": "mod"}),
        posting={}, resume={},
    )
    assert "contractions are fine." in prompt
    assert "No exclamation marks" in prompt
