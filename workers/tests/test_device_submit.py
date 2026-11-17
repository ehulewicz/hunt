"""`os hunt apply --submit` pressing Submit through the device agent: the run's side."""

from __future__ import annotations

from typing import Any

import pytest
from test_submit import CONFIRM, chromium, form, page, presses, quick, session_on  # noqa: F401

from personal_os_worker.jobs import device_submit
from personal_os_worker.jobs.page_state import ApplicationPageState
from personal_os_worker.jobs.submit import (
    ApplicationHeld,
    ApplicationRejected,
    ApplicationUnconfirmed,
)

LEVER = "https://jobs.lever.co/acme/abc-123/apply"


class FakeLink:
    """The agent over the link: answers `press_ready` and `press` as told."""

    def __init__(self, events: list[str], *, ready: dict[str, Any] | None,
                 outcome: dict[str, Any] | None) -> None:
        self.events = events
        self.ready = ready
        self.outcome = outcome
        self.sent: list[dict[str, Any]] = []

    def request(self, message: dict[str, Any], reply: str, *, timeout: float) -> Any:
        self.sent.append(message)
        self.events.append(str(message["type"]))
        return self.ready if reply == "press_ready" else self.outcome


class FakeBrowser:
    def __init__(self, tab: Any, events: list[str]) -> None:
        self.page = tab
        self.events = events

    def active_page(self, _current: Any = None) -> Any:
        return self.page

    def let_go(self) -> None:
        self.events.append("let_go")

    def take_back(self, target: str, _errors: Any = None) -> Any:
        self.events.append(f"take_back {target}")
        return self.page


def on_lever(tab: Any, markup: str) -> None:
    tab.route("https://jobs.lever.co/**",
              lambda route: route.fulfill(body=markup, content_type="text/html"))
    tab.goto(LEVER)


def linked(tab: Any, *, ready: dict[str, Any] | None = None,
           outcome: dict[str, Any] | None = None) -> tuple[Any, FakeLink, list[str]]:
    events: list[str] = []
    link = FakeLink(events, ready={"type": "press_ready", "ok": True} if ready is None else ready,
                    outcome=outcome)
    session = session_on(tab)
    session.ctx.link = link
    session.ctx.listing_id = "L1"
    session.ctx.browser = FakeBrowser(tab, events)
    return session, link, events


def device_form() -> str:
    return form(CONFIRM).replace('id="go"', 'id="btn-submit"')


def test_a_submitted_press_through_the_device_never_clicks_with_playwright(
    page: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    said: list[str] = []
    monkeypatch.setattr(device_submit.console, "print", lambda text, **_: said.append(text))
    on_lever(page, device_form())
    session, link, events = linked(page, outcome={"type": "press_outcome",
                                                  "outcome": "submitted", "url": LEVER})
    assert session.press_forward([], {}) == ApplicationPageState.SUBMITTED
    assert presses(page) == 0
    assert events[:3] == ["press_ready", "let_go", "press"]
    assert events[3].startswith("take_back ")
    press = link.sent[1]
    assert press["listing_id"] == "L1" and press["url"] == LEVER
    assert press["selector"] == "#btn-submit" and press["experiment"] == 1
    assert press["target"] == events[3].split(" ", 1)[1]
    assert any("pressing Submit through the device (experiment 1)" in line for line in said)
    assert session.device_pressed and session.state.submit_pressed


@pytest.mark.parametrize(
    ("outcome", "ending"),
    [
        ({"outcome": "captcha_rejected"}, ApplicationRejected),
        ({"outcome": "challenge_shown"}, ApplicationHeld),
        ({"outcome": "unconfirmed"}, ApplicationUnconfirmed),
        ({"outcome": "validation"}, ApplicationUnconfirmed),
        ({"outcome": None, "error": "the selector matches nothing"}, ApplicationUnconfirmed),
        (None, ApplicationUnconfirmed),
    ],
)
def test_every_other_outcome_ends_the_run_and_nothing_presses_again(
    page: Any, outcome: dict[str, Any] | None, ending: type[Exception],  # noqa: F811
) -> None:
    on_lever(page, device_form())
    answer = {"type": "press_outcome", **outcome} if outcome is not None else None
    session, link, events = linked(page, outcome=answer)
    with pytest.raises(ending):
        session.press_forward([], {})
    assert presses(page) == 0
    assert [message["type"] for message in link.sent] == ["press_ready", "press"]
    assert events.index("let_go") < events.index("press")


def test_a_button_with_no_plain_id_is_marked_for_the_agent(page: Any) -> None:  # noqa: F811
    on_lever(page, form(CONFIRM).replace('id="go"', ""))
    session, link, _events = linked(page, outcome={"type": "press_outcome",
                                                   "outcome": "submitted"})
    assert session.press_forward([], {}) == ApplicationPageState.SUBMITTED
    assert link.sent[1]["selector"] == f"[{device_submit.MARK}]"
    assert page.locator(f"[{device_submit.MARK}]").count() == 1


def test_an_agent_that_will_not_press_leaves_the_press_to_playwright(
    page: Any,  # noqa: F811
) -> None:
    on_lever(page, device_form())
    session, _link, events = linked(
        page, ready={"type": "press_ready", "ok": False, "reason": "the drive switch is off"})
    assert session.press_forward([], {}) == ApplicationPageState.SUBMITTED
    assert presses(page) == 1
    assert events == ["press_ready"]


def test_an_agent_that_does_not_answer_leaves_the_press_to_playwright(
    page: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    on_lever(page, device_form())
    session, link, events = linked(page)
    link.ready = None
    assert session.press_forward([], {}) == ApplicationPageState.SUBMITTED
    assert presses(page) == 1 and events == ["press_ready"]


@pytest.mark.parametrize(("state", "ending"), [("burned", ApplicationRejected),
                                               ("unconfirmed", ApplicationUnconfirmed),
                                               ("pending", ApplicationUnconfirmed)])
def test_a_listing_the_agent_holds_locked_is_pressed_by_nothing(
    page: Any, state: str, ending: type[Exception],  # noqa: F811
) -> None:
    on_lever(page, device_form())
    session, _link, events = linked(
        page, ready={"type": "press_ready", "ok": False, "reason": "locked", "locked": state})
    with pytest.raises(ending):
        session.press_forward([], {})
    assert presses(page) == 0 and events == ["press_ready"]


def test_another_host_or_no_listing_never_asks_the_agent(page: Any) -> None:  # noqa: F811
    page.set_content(device_form())
    session, _link, events = linked(page)
    assert session.press_forward([], {}) == ApplicationPageState.SUBMITTED
    assert presses(page) == 1 and events == []
    on_lever(page, device_form())
    session, _link, events = linked(page)
    session.ctx.listing_id = ""
    assert session.press_forward([], {}) == ApplicationPageState.SUBMITTED
    assert presses(page) == 1 and events == []


def test_the_press_hosts_are_lever_alone_for_now() -> None:
    from personal_os_worker.device.drive_state import ATS_HOSTS, DEVICE_PRESS_HOSTS

    assert sorted(DEVICE_PRESS_HOSTS) == ["jobs.lever.co"]
    assert all(host in ATS_HOSTS for host in DEVICE_PRESS_HOSTS)
