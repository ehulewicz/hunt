"""Every learned file keeps its contract: the policy, then its own examples.

The repository's learned folders ship empty today, so this is vacuous for them;
the reference examples under `fixtures/learned_examples/` prove each category
end to end and keep the fixer's prompt examples from rotting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import personal_os_worker.jobs.registry as registry
from personal_os_worker.jobs.fix.examples import _check
from personal_os_worker.jobs.registry import policy

try:
    from playwright.sync_api import Error, sync_playwright
except ImportError:  # pragma: no cover
    Error = Exception


@pytest.fixture(scope="module")
def browser_page():  # type: ignore[no-untyped-def]
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as error:
            pytest.skip(f"browser launch is restricted: {error}")
        page = browser.new_page()
        yield page
        browser.close()

LEARNED = Path(__file__).parent.parent / "src" / "personal_os_worker" / "jobs" / "registry"
EXAMPLES = Path(__file__).parent / "fixtures" / "learned_examples"

CATEGORIES = ("kinds", "strategies", "formats", "questions", "routes")


def _repo_learned_files() -> list[tuple[str, Path]]:
    files = []
    for category in CATEGORIES:
        folder = LEARNED / category / "learned"
        files.extend(
            (category, path)
            for path in sorted(folder.glob("*.py"))
            if not path.name.startswith("_")
        )
    return files


@pytest.mark.parametrize(
    ("category", "path"), _repo_learned_files(), ids=lambda item: getattr(item, "name", item)
)
def test_a_repo_learned_file_passes_policy_and_examples(category: str, path: Path) -> None:
    assert policy.check_file(path, category=category) == []
    from personal_os_worker.jobs.fix import examples

    assert examples.main([str(path), str(path.parent / path.stem)]) == 0


def _reference_files() -> list[tuple[str, Path]]:
    files = []
    for category in CATEGORIES:
        path = EXAMPLES / f"{category}.py"
        if path.is_file():
            files.append((category, path))
    return files


@pytest.mark.parametrize(
    ("category", "path"), _reference_files(), ids=lambda item: getattr(item, "name", item)
)
def test_a_reference_example_passes_policy_and_its_examples(
    category: str, path: Path, browser_page
) -> None:
    assert policy.check_file(path, category=category) == []
    declarations = registry.load_file(path, category=category)
    assert declarations
    registry.reload_learned()
    failures = _check(declarations, EXAMPLES / category, browser_page)
    assert failures == []
