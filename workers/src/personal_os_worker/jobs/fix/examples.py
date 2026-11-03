"""`python -m personal_os_worker.jobs.fix.examples FILE FIXTURE_DIR`: a declaration's
own examples, run in a protected process before a fix is ever kept.

A learned file proves itself on saved HTML: formats render their FormatInputs;
strategies operate a WidgetHandle on the selector and read back the way
verify_control would; kinds capture and read; question readers name the group.
Exit 0 when every example holds, 1 while any fails — with each failure named.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from personal_os_worker.jobs import registry
from personal_os_worker.jobs.registry.api import (
    Format,
    Kind,
    NoMatch,
    QuestionReader,
    Refused,
    Route,
    Strategy,
    WidgetHandle,
)


def _check(declarations: list[Any], fixture_dir: Path, page: Any = None) -> list[str]:
    """Each failing example, described."""
    failures: list[str] = []
    for declaration in declarations:
        if isinstance(declaration, Format):
            failures.extend(_format_failures(declaration))
        elif isinstance(declaration, Kind):
            failures.extend(_kind_failures(declaration, fixture_dir, page))
        elif isinstance(declaration, Strategy):
            failures.extend(_strategy_failures(declaration, fixture_dir, page))
        elif isinstance(declaration, QuestionReader):
            failures.extend(_reader_failures(declaration, fixture_dir, page))
        elif isinstance(declaration, Route):
            continue
    return failures


def _format_failures(declaration: Format) -> list[str]:
    failures = []
    for index, (fmt_input, expected) in enumerate(declaration.examples):
        try:
            actual = declaration.render(fmt_input)
        except Exception as error:
            failures.append(f"format example {index}: raised {error}")
            continue
        if actual != expected:
            failures.append(
                f"format example {index}: {actual!r}, expected {expected!r}"
            )
    return failures


def _kind_failures(declaration: Kind, fixture_dir: Path, page: Any = None) -> list[str]:
    failures = []
    for example in declaration.examples:
        page = _fixture_page(fixture_dir / example.fixture, page)
        if page is None:
            failures.append(f"kind example {example.fixture}: no fixture")
            continue
        failures.extend(_kind_read(page, declaration, example))
    return failures


def _strategy_failures(declaration: Strategy, fixture_dir: Path, page: Any = None) -> list[str]:
    failures = []
    for example in declaration.examples:
        page = _fixture_page(fixture_dir / example.fixture, page)
        if page is None:
            failures.append(f"strategy example {example.fixture}: no fixture")
            continue
        try:
            control = page.locator(example.selector).first
            declaration.operate(WidgetHandle(page, control), example.value)
            actual = _read_back(page, control)
        except (Refused, NoMatch) as error:
            failures.append(f"strategy example {example.fixture}: {error}")
            continue
        if actual != example.expected:
            failures.append(
                f"strategy example {example.fixture}: read {actual!r},"
                f" expected {example.expected!r}"
            )
    return failures


def _reader_failures(declaration: QuestionReader, fixture_dir: Path, page: Any = None) -> list[str]:
    failures = []
    for example in declaration.examples:
        page = _fixture_page(fixture_dir / example.fixture, page)
        if page is None:
            failures.append(f"question example {example.fixture}: no fixture")
            continue
        try:
            text = page.locator(example.selector).first.evaluate(
                """(element, [group_selector, question_selector]) => {
  const group = element.closest(group_selector);
  return group
    ? (group.querySelector(question_selector)?.textContent || '')
        .replace(/\\s+/g, ' ').trim()
    : '';
}""",
                [declaration.group_selector, declaration.question_selector],
            )
        except Exception as error:
            failures.append(f"question example {example.fixture}: {error}")
            continue
        if str(text or "") != example.expected:
            failures.append(
                f"question example {example.fixture}: {text!r},"
                f" expected {example.expected!r}"
            )
    return failures


def _kind_read(page: Any, declaration: Kind, example: Any) -> list[str]:
    """Capture the widget by its reads and compare the read value."""
    control = page.locator(example.selector).first
    try:
        actual = _read_back(page, control, reads=declaration.reads)
    except Exception as error:
        return [f"kind example {example.fixture}: {error}"]
    if actual != example.expected:
        return [
            f"kind example {example.fixture}: read {actual!r},"
            f" expected {example.expected!r}"
        ]
    return []


def _read_back(page: Any, control: Any, reads: str = "") -> str:
    """What the control holds, the way verify_control would read the kind."""
    script = {
        "text": "element => String(element.innerText || element.textContent || '').trim()",
        "selected": """element => (element.tagName === 'SELECT'
  ? [...element.selectedOptions]
  : [...element.querySelectorAll(
      '[aria-selected="true"], [aria-pressed="true"], [aria-checked="true"]')])
    .map(node => String(node.textContent || '').trim()).join(', ')""",
    }.get(reads or "value")
    if script is None and str(reads).startswith("attribute:"):
        name = str(reads)[len("attribute:"):]
        script = f"element => String(element.getAttribute({name!r}) || '')"
    if script is None:
        tag = str(control.evaluate("element => element.tagName")).casefold()
        script = (
            "element => String(element.innerText || '').trim()"
            if tag not in {"input", "textarea", "select"}
            else "element => String(element.value || '')"
        )
    return str(control.evaluate(script))


def _fixture_page(path: Path, page: Any = None) -> Any | None:
    if not path.is_file():
        return None
    if page is not None:
        page.set_content(path.read_text())
        return page
    from playwright.sync_api import sync_playwright

    manager = sync_playwright().start()
    browser = manager.chromium.launch(headless=True)
    new_page = browser.new_page()
    new_page.goto(path.resolve().as_uri())
    return new_page


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m personal_os_worker.jobs.fix.examples FILE FIXTURE_DIR")
        return 2
    file_path, fixture_dir = Path(args[0]), Path(args[1])
    site = ""
    declarations: list[Any] = []
    for category in registry.CATEGORIES:
        try:
            found = registry.load_file(file_path, category=category)
        except ValueError as error:
            print(error)
            continue
        if found:
            declarations = found
            site = ", ".join(found[0].sites)
            break
    if not declarations:
        print(f"{file_path}: no declaration the policy accepts")
        return 1
    failures = _check(declarations, fixture_dir)
    for failure in failures:
        print(f"  FAIL {failure}")
    if failures:
        return 1
    print(f"{file_path}: every example holds ({site})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
