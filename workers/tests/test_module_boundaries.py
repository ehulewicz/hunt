"""The dependency direction between verticals, enforced.

`jobs` may use `documents`, both may use `core`, and nothing points back up.
Without this test the direction erodes one convenient import at a time: before
it existed, `core.tables` reached into `jobs.adapters` for ATS detection and
`core.doctor` reached into `documents.resume_pdf` for the LaTeX preamble.

`cli.py` at the package root is the composition root. Wiring every vertical
together is its whole job, so it is exempt by design rather than by oversight.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "personal_os_worker"

# layer -> layers it must never import
FORBIDDEN = {
    "core": ("jobs", "documents"),
    "documents": ("jobs",),
    "jobs": (),
}


def imported_layers(module: Path) -> set[str]:
    """Every personal_os_worker layer this module imports, lazy imports included."""
    tree = ast.parse(module.read_text(), filename=str(module))
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        for name in names:
            parts = name.split(".")
            if len(parts) >= 2 and parts[0] == "personal_os_worker":
                found.add(parts[1])
    return found


def test_layers_never_import_upward() -> None:
    violations: list[str] = []
    for layer, forbidden in FORBIDDEN.items():
        for module in sorted((PACKAGE / layer).rglob("*.py")):
            for imported in sorted(imported_layers(module) & set(forbidden)):
                violations.append(f"{module.relative_to(PACKAGE)} imports {imported}")
    assert not violations, "upward imports:\n  " + "\n  ".join(violations)


def test_the_check_can_actually_see_lazy_imports() -> None:
    """A guard that fails open is worse than none; prove it reads function bodies."""
    source = ast.parse("def f():\n    from personal_os_worker.jobs.browser import VisibleBrowser\n")
    found: set[str] = set()
    for node in ast.walk(source):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[1])
    assert found == {"jobs"}
