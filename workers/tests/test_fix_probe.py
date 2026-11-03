"""The target-specific Kind probe: a candidate must capture the widget the
problem named, on this site, not anything anywhere on the page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from test_autofill_session import browser_page as browser_page

from personal_os_worker.jobs import registry
from personal_os_worker.jobs.fix import probe
from personal_os_worker.jobs.resolve import Knowledge

PAGE = (
    '<label for="a">Rate Python</label>'
    '<div id="a" role="slider" tabindex="0" aria-valuemin="0"'
    ' aria-valuemax="5" aria-valuenow="0"></div>'
    '<div><p>Rate Java</p>'
    '<div id="b" role="slider" tabindex="0" aria-valuemin="0"'
    ' aria-valuemax="5" aria-valuenow="0"></div></div>'
)


def _kind_file(tmp_path: Path, name: str, selector: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(f'''from personal_os_worker.jobs.registry.api import Kind

KIND = Kind(
    name="{name}",
    selector="{selector}",
    reads="attribute:aria-valuenow",
    strategies=("click_div",),
    sites=("fixture.example",),
    structure="x",
    proven=False,
    examples=(),
)
''')
    return path


def test_a_kind_probe_must_capture_the_problem_widget(
    browser_page: Any, tmp_path: Path
) -> None:
    browser_page.route(
        "https://fixture.example/**",
        lambda route: route.fulfill(body=PAGE, content_type="text/html"),
    )
    browser_page.goto("https://fixture.example/apply")
    target = {"kind": "field", "question": "Rate Python"}
    registry.reload_learned()
    try:
        registry.reload_learned()
        [other] = registry.load_file(
            _kind_file(tmp_path, "rate_b", "#b"), category="kinds"
        )
        result = probe.probe_candidate(
            browser_page, target, Knowledge(), [other]
        )
        assert result["ok"] is False
        assert "Rate Python" in str(result["note"])
        registry.reload_learned()
        [kind] = registry.load_file(
            _kind_file(tmp_path, "rate_a", "#a"), category="kinds"
        )
        result = probe.probe_candidate(
            browser_page, target, Knowledge(), [kind]
        )
        assert result["ok"] is True
    finally:
        registry.reload_learned()
