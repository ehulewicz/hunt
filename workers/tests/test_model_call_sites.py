"""Every `claude -p` call goes through `jobs/model.py`, and only the call sites
AGENTS.md names may ask it to run one.

A module is a call site when it references the model's `run`: an attribute
`.run` on a name bound to the `personal_os_worker.jobs.model` module (a
`partial(model.run, purpose=...)` counts), or a direct
`from personal_os_worker.jobs.model import run`. Building a `claude` argv by
hand — a literal starting with `"claude"`, or one of the flags only
`model.argv` may write — is forbidden everywhere else.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "personal_os_worker"
REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL = Path("jobs/model.py")

# Every module besides model.py allowed to ask it to run a call, parked ones
# included; AGENTS.md must name each one too.
CALL_SITES = {
    "jobs/answers.py",
    "jobs/short_answers.py",
    "jobs/answer_check.py",
    "jobs/compose.py",
    "jobs/cover_letter.py",
    "jobs/fix/lane.py",
    "jobs/grad_date/selection.py",
}

# Flags only `model.argv` may write into an argv.
ARGV_PARTS = ("claude", "--json-schema", "--no-session-persistence",
              "--safe-mode", "--output-format")


def _model_names(tree: ast.AST) -> set[str]:
    """Names a module binds to the `jobs.model` module or its `run`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "personal_os_worker.jobs":
                names.update(
                    alias.asname or alias.name
                    for alias in node.names if alias.name == "model"
                )
            elif node.module == "personal_os_worker.jobs.model":
                names.update(
                    alias.asname or alias.name
                    for alias in node.names if alias.name == "run"
                )
        elif isinstance(node, ast.Import):
            names.update(
                alias.asname or alias.name.rsplit(".", 1)[-1]
                for alias in node.names
                if alias.name == "personal_os_worker.jobs.model"
            )
    return names


def _references_model_run(tree: ast.AST) -> bool:
    names = _model_names(tree)
    return any(
        isinstance(node, ast.Attribute) and node.attr == "run"
        and isinstance(node.value, ast.Name) and node.value.id in names
        for node in ast.walk(tree)
    ) or any(
        isinstance(node, ast.ImportFrom)
        and node.module == "personal_os_worker.jobs.model"
        and any(alias.name == "run" for alias in node.names)
        for node in ast.walk(tree)
    )


def _starts_claude(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "claude":
                return True
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in ARGV_PARTS[1:]
        ):
            return True
    return False


def _modules() -> dict[Path, ast.AST]:
    return {
        path.relative_to(PACKAGE): ast.parse(path.read_text(), filename=str(path))
        for path in sorted(PACKAGE.rglob("*.py"))
        if path.relative_to(PACKAGE) != MODEL
    }


def test_only_the_named_call_sites_run_the_model() -> None:
    sites = {
        str(relative) for relative, tree in _modules().items()
        if _references_model_run(tree)
    }
    assert sites == CALL_SITES


def test_only_model_builds_a_claude_argv() -> None:
    offenders = [
        str(relative) for relative, tree in _modules().items() if _starts_claude(tree)
    ]
    assert not offenders, "claude argv parts outside jobs/model.py: " + ", ".join(offenders)


def test_agents_md_names_every_call_site() -> None:
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    missing = [
        site for site in sorted(CALL_SITES)
        if f"`{site.removeprefix('jobs/')}`" not in text
    ]
    assert not missing, f"call sites missing from AGENTS.md: {missing}"


def test_the_checks_see_a_handbuilt_argv_and_an_aliased_run() -> None:
    """A guard that fails open is worse than none; prove both checks read source."""
    source = ast.parse(
        "import personal_os_worker.jobs.model as m\n"
        "run = m.run\n"
        'argv = ["claude", "-p", "--safe-mode"]\n'
    )
    assert _references_model_run(source) is True
    assert _starts_claude(source) is True
    clean = ast.parse("from personal_os_worker.jobs import model\nmodel.argv('x')\n")
    assert _references_model_run(clean) is False
    assert _starts_claude(clean) is False
