"""The registry API: the only module a learned file may import (Phase 3b).

A learned file declares what it knows as a frozen dataclass — a Kind, a
Strategy, a Format, a QuestionReader, a Route — bound to one registrable site.
The worker turns declarations into fills; a learned file never sees Playwright,
the page, or another fact. The two handles are the only window it gets on a
live page, and both refuse anything that submits, signs in, or navigates.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from personal_os_worker.jobs.options import choose_option
from personal_os_worker.jobs.registry.strategies import CONTROL_TIMEOUT_MS
from personal_os_worker.jobs.safety import SUBMITTING

if TYPE_CHECKING:
    from typing import Any

VERSION = 1

# What a Kind declaration may ask the worker to read; the worker turns one into
# the read script. Never JavaScript from the declaration.
READS = ("text", "value", "selected", "attribute:")


@dataclass(frozen=True)
class Kind:
    """A control kind a site needs: what selects it, what to read, its strategies.

    `sites` is every site this kind was kept for; `structure` is the widget's
    problem structure (its control key or unknown signature). `proven` flips
    True once a run it did not enter in makes it work.
    """

    name: str
    selector: str
    reads: str
    strategies: tuple[str, ...]
    sites: tuple[str, ...]
    structure: str = ""
    proven: bool = False
    examples: tuple[Example, ...] = ()


@dataclass(frozen=True)
class Strategy:
    """A named way to operate one kind: set the value, return what was set."""

    name: str
    kind: str
    operate: Callable[[WidgetHandle, str], str | None]
    sites: tuple[str, ...]
    structure: str = ""
    proven: bool = False
    examples: tuple[Example, ...] = ()


@dataclass(frozen=True)
class FormatInput:
    """What a Format sees: the fact's value and the control's words, never the page."""

    value: object
    value_type: str
    options: tuple[str, ...]
    question: str
    input_type: str


@dataclass(frozen=True)
class Format:
    """A named way to write one or more facts for a control; its `fact_keys`
    are its type."""

    name: str
    fact_keys: tuple[str, ...]
    render: Callable[[FormatInput], str | None]
    sites: tuple[str, ...]
    proven: bool = False
    examples: tuple[tuple[FormatInput, str | None], ...] = ()


@dataclass(frozen=True)
class QuestionReader:
    """How a site's captured radio group without a question names itself:

    the nearest ancestor matching `group_selector`, the text of
    `question_selector` inside it.
    """

    name: str
    group_selector: str
    question_selector: str
    sites: tuple[str, ...]
    structure: str = ""
    proven: bool = False
    examples: tuple[Example, ...] = ()


@dataclass(frozen=True)
class Route:
    """How to reach a posting or its form on one site (title, company);
    `sites` always holds exactly one."""

    sites: tuple[str, ...]
    goal: Literal["posting", "form"]
    run: Callable[[SiteHandle, str, str], bool]
    proven: bool = False
    examples: tuple[Example, ...] = ()


class Refused(RuntimeError):
    """A handle was asked to submit, sign in, or leave the site."""


class NoMatch(RuntimeError):
    """A pick found no option matching the value: a format stop, not a safety one."""


@dataclass(frozen=True)
class Example:
    """One checkable example of a declaration on a saved fixture page.

    `fixture` is an HTML file in the fix's fixture folder; `selector` the
    control's CSS selector in it; `value` what the strategy or kind is run
    with; `expected` what must read back (a kind's read, a reader's question).
    """

    fixture: str
    selector: str
    value: str
    expected: str


# Keys a handle will press: navigation and editing, never Enter.
ALLOWED_KEYS = frozenset(
    {
        "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
        "Home", "End", "PageUp", "PageDown",
        "Tab", "Escape", "Space", "Backspace", "Delete",
    }
)
REFUSED_WORDS = re.compile(
    r"^\s*next\s*$|\b(?:next|continue)\s+(?:step|page)\b|\bsign\s*in\b|\blog\s*in\b",
    re.IGNORECASE,
)
WAIT_CAP_MS = 4_000


def _refused(text: str) -> bool:
    return SUBMITTING.search(text) is not None or REFUSED_WORDS.search(text) is not None


class WidgetHandle:
    """A window on one control: operate it, read it, never the page around it."""

    def __init__(self, page: Any, control: Any) -> None:
        self._page = page
        self._control = control

    def _name(self) -> str:
        texts = []
        with contextlib.suppress(Exception):
            texts.append(str(self._control.inner_text()))
        with contextlib.suppress(Exception):
            texts.append(str(self._control.get_attribute("aria-label") or ""))
        with contextlib.suppress(Exception):
            texts.append(str(self._control.get_attribute("value") or ""))
        return " ".join(texts)

    def click(self) -> None:
        """Click the control, unless it reads as submit, next, or sign in."""
        if _refused(self._name()):
            raise Refused(f"control reads as submit or sign in: {self._name()!r}")
        self._control.click(timeout=CONTROL_TIMEOUT_MS)

    def type(self, text: str) -> None:
        """Clear the control, then type the text into it."""
        self._control.fill("", timeout=CONTROL_TIMEOUT_MS)
        self._control.press_sequentially(text, timeout=CONTROL_TIMEOUT_MS)

    def press(self, key: str) -> None:
        """Press a navigation or editing key; never Enter or anything else."""
        if key not in ALLOWED_KEYS:
            raise Refused(f"key {key!r} is not in the allowed set")
        self._control.press(key, timeout=CONTROL_TIMEOUT_MS)

    def pick(self, text: str) -> str:
        """Pick the option matching the text: a select's label, else a menu item."""
        if _refused(self._name()):
            raise Refused(f"control reads as submit or sign in: {self._name()!r}")
        tag = str(self._control.evaluate("element => element.tagName")).casefold()
        if tag == "select":
            options = self._control.evaluate(
                "element => [...element.options].map(option => option.textContent.trim())"
            )
            chosen = choose_option([str(option) for option in options], text)
            if chosen is None:
                raise NoMatch(f"no option matches {text!r}")
            self._control.select_option(label=chosen, timeout=CONTROL_TIMEOUT_MS)
            return chosen
        owned_id = self._control.get_attribute(
            "aria-controls", timeout=CONTROL_TIMEOUT_MS
        ) or self._control.get_attribute("aria-owns", timeout=CONTROL_TIMEOUT_MS)
        items = self._control.locator('[role="option"], li')
        if owned_id:
            items = self._control.locator(
                f"xpath=ancestor::body//*[@id={owned_id!r}]"
            ).locator('[role="option"], li')
        texts = [
            str(items.nth(index).inner_text()).strip() for index in range(items.count())
        ]
        chosen = choose_option([text for text in texts if text], text)
        if chosen is None:
            raise NoMatch(f"no option matches {text!r}")
        items.nth(texts.index(chosen)).click(timeout=CONTROL_TIMEOUT_MS)
        return chosen

    def attribute(self, name: str) -> str | None:
        """One attribute of the control."""
        value = self._control.get_attribute(name, timeout=CONTROL_TIMEOUT_MS)
        return None if value is None else str(value)

    def text(self) -> str:
        """What the control holds: its value when it has one, else its text."""
        tag = str(self._control.evaluate("element => element.tagName")).casefold()
        if tag in {"input", "textarea"}:
            return str(self._control.input_value(timeout=CONTROL_TIMEOUT_MS))
        return str(self._control.inner_text(timeout=CONTROL_TIMEOUT_MS))

    def selected(self) -> list[str]:
        """The selected options, or the texts of the control's pressed pills."""
        tag = str(self._control.evaluate("element => element.tagName")).casefold()
        if tag == "select":
            return [
                str(text)
                for text in self._control.evaluate(
                    "element => [...element.selectedOptions]"
                    ".map(option => option.textContent.trim())"
                )
            ]
        pressed = self._control.locator(
            '[aria-pressed="true"], [aria-selected="true"], [aria-checked="true"]'
        )
        return [
            str(pressed.nth(index).inner_text()).strip()
            for index in range(pressed.count())
        ]

    def wait(self, ms: int) -> None:
        """Wait, capped at four seconds."""
        self._page.wait_for_timeout(min(int(ms), WAIT_CAP_MS))

    def wait_for_text(self, text: str, timeout_ms: int = 1000) -> bool:
        """Whether the text appears on the page within the cap."""
        try:
            self._page.get_by_text(text).first.wait_for(
                state="visible", timeout=min(int(timeout_ms), WAIT_CAP_MS)
            )
            return True
        except Exception:
            return False


class SiteHandle:
    """A window on one site: its search, its links, same-site navigation only."""

    def __init__(self, page: Any) -> None:
        from personal_os_worker.jobs.browser import registrable_site

        self._page = page
        self._site = registrable_site(str(page.url))

    def _refused_link(self, candidate: Any) -> bool:
        text = " ".join(
            part
            for part in (
                str(candidate.inner_text()),
                str(candidate.get_attribute("aria-label") or ""),
            )
            if part
        )
        if _refused(text):
            return True
        href = str(candidate.get_attribute("href") or "")
        if href and not href.startswith(("#", "javascript:")):
            from personal_os_worker.jobs.browser import registrable_site

            if registrable_site(href) != self._site:
                return True
        return False

    def click_text(self, text: str) -> bool:
        """Click the one visible link or button by this text; refused ones don't count."""
        for role in ("link", "button"):
            candidates = self._page.get_by_role(role, name=re.compile(
                rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE
            ))
            found = []
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                try:
                    if candidate.is_visible() and not self._refused_link(candidate):
                        found.append(candidate)
                except Exception:
                    continue
            if len(found) == 1:
                found[0].click(timeout=CONTROL_TIMEOUT_MS)
                return True
        return False

    def search(self, query: str) -> bool:
        """Fill the site's job search box and submit it."""
        from personal_os_worker.jobs.navigate import search_box

        box = search_box(self._page)
        if box is None:
            return False
        box.fill(query, timeout=CONTROL_TIMEOUT_MS)
        box.press("Enter", timeout=CONTROL_TIMEOUT_MS)
        self._page.wait_for_timeout(2000)
        return True

    def open(self, url: str) -> bool:
        """Open a URL on the same registrable site; anything else is refused."""
        from personal_os_worker.jobs.browser import registrable_site

        if registrable_site(url) != self._site:
            return False
        self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        return True

    def links(self) -> list[str]:
        """The visible links' texts, in order."""
        seen: dict[str, None] = {}
        for index in range(self._page.locator("a[href]").count()):
            try:
                link = self._page.locator("a[href]").nth(index)
                if link.is_visible():
                    seen.setdefault(str(link.inner_text()).strip(), None)
            except Exception:
                continue
        return [text for text in seen if text]

    def text(self) -> str:
        """The page's visible text, capped at twenty thousand characters."""
        parts = []
        for frame in self._page.frames:
            try:
                parts.append(frame.locator("body").inner_text(timeout=5_000))
            except Exception:
                continue
        return "\n".join(part for part in parts if part.strip())[:20000]

    def url(self) -> str:
        """Where the page is."""
        return str(self._page.url)

