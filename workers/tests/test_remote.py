import io
import threading
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from personal_os_worker.jobs import remote
from personal_os_worker.jobs.audit import Finding
from personal_os_worker.jobs.keys import NoKeys
from personal_os_worker.jobs.remote import ConsoleTee, LinkedKeys, RunLink, gap_items


class FakeClient:
    """A LinkClient stand-in: records what is sent, and can deliver agent messages."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.deliver: Any = None

    def start(self, on_message: Any) -> None:
        self.deliver = on_message

    def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True


def linked() -> tuple[RunLink, FakeClient, list[int]]:
    client = FakeClient()
    interrupts: list[int] = []
    return RunLink(client, interrupt=lambda: interrupts.append(1)), client, interrupts  # type: ignore[arg-type]


def test_the_tee_passes_everything_through_and_sends_whole_lines() -> None:
    original = io.StringIO()
    lines: list[list[dict[str, Any]]] = []
    tee = ConsoleTee(original, lines.append)
    tee.write("\x1b[32mpattern\x1b[0m Name")
    assert lines == []  # not a whole line yet
    tee.write(" -> Ethan\n\nnext")
    assert original.getvalue() == "\x1b[32mpattern\x1b[0m Name -> Ethan\n\nnext"
    assert lines == [[{"text": "pattern", "style": ["green"]}, {"text": " Name -> Ethan"}], []]
    assert tee.isatty() is False


def test_a_rich_console_on_the_tee_mirrors_its_styles() -> None:
    lines: list[list[dict[str, Any]]] = []
    console = Console(file=io.StringIO(), force_terminal=True, color_system="standard", width=80)
    console.file = ConsoleTee(console.file, lines.append)
    console.print("[yellow]Warning:[/yellow] left for you")
    assert lines == [[{"text": "Warning:", "style": ["yellow"]}, {"text": " left for you"}]]


def test_only_the_pages_controls_reach_the_run() -> None:
    link, client, interrupts = linked()
    keys = LinkedKeys(link, NoKeys())
    client.deliver({"type": "control", "action": "done"})
    client.deliver({"type": "control", "action": "rm -rf"})
    with keys:
        assert keys.wait(0) == {"done"}
        assert keys.wait(0) == set()
    assert interrupts == []


def test_a_remote_stop_is_ctrl_c() -> None:
    link, client, interrupts = linked()
    client.deliver({"type": "control", "action": "stop"})
    assert interrupts == [1]
    assert link.controls() == set()


def test_fact_answers_queue_until_the_watch_loop_takes_them() -> None:
    link, client, _ = linked()
    client.deliver({"type": "fact", "question": "GPA", "key": "education.gpa", "value": "3.78"})
    assert link.fact_answers() == [
        {"type": "fact", "question": "GPA", "key": "education.gpa", "value": "3.78"}
    ]
    assert link.fact_answers() == []


def test_the_letter_goes_to_the_page_and_the_edit_comes_back(tmp_path: Path) -> None:
    link, client, _ = linked()
    letter = tmp_path / "cover_letter.md"
    letter.write_text("Dear Acme,\n[hook]\n")

    def page_edits() -> None:
        while not client.sent:
            threading.Event().wait(0.01)
        client.deliver({"type": "letter", "text": "Dear Acme,\nI build things.\n"})

    threading.Thread(target=page_edits, daemon=True).start()
    link.edit_letter(letter)
    assert client.sent[0] == {"type": "letter", "text": "Dear Acme,\n[hook]\n"}
    assert letter.read_text() == "Dear Acme,\nI build things.\n"


def test_a_letter_left_when_the_agent_goes_away_keeps_the_draft(tmp_path: Path) -> None:
    link, client, _ = linked()
    letter = tmp_path / "cover_letter.md"
    letter.write_text("Draft\n")
    client.closed = True
    link.edit_letter(letter)
    assert letter.read_text() == "Draft\n"


def test_the_tab_is_reported_once_per_change() -> None:
    link, client, _ = linked()
    targets = {"a": "T1", "b": "T2"}
    link._target_of = lambda page: targets[page]  # type: ignore[method-assign]
    link.tab("a")  # type: ignore[arg-type]
    link.tab("a")  # type: ignore[arg-type]
    link.tab("b")  # type: ignore[arg-type]
    assert [message for message in client.sent if message["type"] == "tab"] == [
        {"type": "tab", "target": "T1"}, {"type": "tab", "target": "T2"},
    ]


def test_gaps_say_what_the_page_can_ask_for() -> None:
    items = gap_items(
        [
            Finding("Date of birth", "fact_gap", True, "no fact identity.birth_date yet",
                    fact_key="identity.birth_date"),
            Finding("GPA", "fact_gap", True, "no fact education.gpa yet", fact_key="education.gpa"),
            Finding("Clearance?", "fact_gap", False, "needs a fact: security clearance status",
                    missing_fact="security clearance status"),
            Finding("Clearance level", "fact_gap", False, "needs a fact",
                    missing_fact="work_auth.clearance_level"),
            Finding("Who referred you?", "fact_gap", True, "no fact referral.referrer_name yet",
                    fact_key="referral.referrer_name"),
            Finding("Pronouns", "pattern_gap", False, "no pattern answers it yet"),
            Finding("Sponsorship", "wrong_answer", True, "says no", fact_key="work_auth.x",
                    answer="Yes"),
        ]
    )
    by_question = {item["question"]: item for item in items}
    assert "Sponsorship" not in by_question
    birth = by_question["Date of birth"]
    assert birth["settable"] and birth["fact_key"] == "identity.birth_date"
    assert birth["label"] == "Date of birth" and birth["value_type"] == "date"
    # The career record decides the GPA; it is edited there, not answered here.
    gpa = by_question["GPA"]
    assert not gpa["settable"]
    assert gpa["hint"] == (
        "education.gpa comes from the career record; edit the record "
        "at /resume/edit or with os hunt resume put"
    )
    unnamed = by_question["Clearance?"]
    assert unnamed["settable"] and unnamed["fact_key"] is None
    assert unnamed["missing_fact"] == "security clearance status"
    assert by_question["Clearance level"]["fact_key"] == "work_auth.clearance_level"
    referral = by_question["Who referred you?"]
    assert not referral["settable"]
    assert referral["hint"] == (
        "referral.referrer_name is set for one application "
        "with os hunt apply --referred NAME"
    )
    assert not by_question["Pronouns"]["settable"]


def test_no_agent_means_no_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote, "socket_path", lambda: tmp_path / "missing.sock")
    assert RunLink.connect(run_id="r", company="Acme", title="Intern", remote=False) is None
