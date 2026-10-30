"""The learned-file policy: the AST checks a file passes before it is imported.

A learned file is data shaped like code: one frozen declaration per category,
written only against `registry/api.py`. These checks run before the file is
ever imported, so a fixer (or a model) can never smuggle page access, the
filesystem, or another module past the registry's boundary.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MAX_LINES = 120

_DECLARATION = {
    "kinds": "Kind",
    "strategies": "Strategy",
    "formats": "Format",
    "questions": "QuestionReader",
    "routes": "Route",
}

_ALLOWED_IMPORT = "personal_os_worker.jobs.registry.api"
_ALLOWED_PACKAGE = "personal_os_worker.jobs.registry"

# Names a learned file may never call or reference: escapes from data to code.
_FORBIDDEN = frozenset(
    {
        "eval", "exec", "compile", "open", "getattr", "setattr", "delattr",
        "type", "globals", "locals", "vars", "__import__", "breakpoint", "input",
        "exit", "quit",
    }
)
# Attributes a learned file may reach: the api's public names, handle methods,
# declaration fields, safe str/list/dict/tuple methods, re, and match methods.
_ALLOWED_ATTRS = frozenset(
    {
        # api.py public names and declaration fields
        "VERSION", "READS", "ALLOWED_KEYS", "Kind", "Strategy", "Format",
        "FormatInput", "QuestionReader", "Route", "Example", "WidgetHandle",
        "SiteHandle", "Refused", "NoMatch",
        "name", "selector", "reads", "strategies", "site", "kind", "operate",
        "fact_keys", "render", "group_selector", "question_selector", "goal",
        "run", "examples", "fixture", "expected", "value", "value_type",
        "options", "question", "input_type",
        # handle methods
        "click", "type", "press", "pick", "attribute", "text", "selected",
        "wait", "wait_for_text", "click_text", "search", "open", "links",
        "url",
        # str
        "strip", "lstrip", "rstrip", "lower", "upper", "casefold", "title",
        "split", "rsplit", "splitlines", "join", "replace", "startswith",
        "endswith", "isdigit", "isalpha", "isalnum", "isnumeric", "count",
        "find", "index", "partition", "rpartition", "zfill", "removeprefix",
        "removesuffix",
        # re + match
        "match", "fullmatch", "findall", "finditer", "sub",
        "compile", "escape", "IGNORECASE", "I", "DOTALL", "S", "MULTILINE",
        "M", "group", "groups", "groupdict", "start", "end", "span",
        # list/dict/tuple
        "append", "extend", "get", "items", "keys", "values",
    }
)

_FORBIDDEN_NODES = (
    ast.While,
    ast.Global,
    ast.Nonlocal,
    ast.ClassDef,
    ast.AsyncFunctionDef,
    ast.AsyncFor,
    ast.AsyncWith,
    ast.Await,
)


def check_source(source: str, *, category: str, site: str | None = None) -> list[str]:
    """Every rule a learned file breaks; an empty list means it may be imported."""
    try:
        declaration = _DECLARATION[category]
    except KeyError:
        return [f"unknown category {category!r}"]
    violations: list[str] = []
    if len(source.splitlines()) > _MAX_LINES:
        violations.append(f"more than {_MAX_LINES} lines")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [f"does not parse: {error}"]
    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN_NODES):
            violations.append(f"{type(node).__name__} is not allowed")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            violations.extend(_import_violations(node))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_"):
            violations.append(f"function {node.name!r} starts with an underscore")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            violations.append(f"name {node.id!r} starts with an underscore")
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN:
            violations.append(f"{node.id} is not allowed")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                violations.append(f"attribute {node.attr!r} starts with an underscore")
            elif node.attr not in _ALLOWED_ATTRS:
                violations.append(f"attribute {node.attr!r} is not allowed")
    violations.extend(_declaration_violations(tree, declaration, site))
    return violations


def check_file(
    path: str | Path, *, category: str, site: str | None = None
) -> list[str]:
    """The policy for one file on disk."""
    return check_source(Path(path).read_text(), category=category, site=site)


def _import_violations(node: ast.Import | ast.ImportFrom) -> list[str]:
    violations = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name != "re":
                violations.append(f"import {alias.name} is not allowed")
        return violations
    if node.module == "__future__":
        for alias in node.names:
            if alias.name != "annotations":
                violations.append(f"from __future__ import {alias.name} is not allowed")
        return violations
    if node.module == _ALLOWED_IMPORT:
        return violations
    if node.module == _ALLOWED_PACKAGE and [a.name for a in node.names] == ["api"]:
        return violations
    violations.append(f"from {node.module} import ... is not allowed")
    return violations


def _declaration_violations(
    tree: ast.Module, declaration: str, site: str | None
) -> list[str]:
    calls = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        value = node.value
        name = ""
        if isinstance(value, ast.Call):
            if isinstance(value.func, ast.Name):
                name = value.func.id
            elif isinstance(value.func, ast.Attribute):
                name = value.func.attr
        if name == declaration:
            calls.append((node, value))
    if len(calls) != 1:
        return [
            f"exactly one module-level {declaration}(...) declaration is required,"
            f" found {len(calls)}"
        ]
    node, call = calls[0]
    if not isinstance(node.targets[0], ast.Name):
        return ["the declaration must be assigned to one name"]
    assert isinstance(call, ast.Call)
    site_kw = next(
        (kw for kw in call.keywords if kw.arg == "site"), None
    )
    if site_kw is None or not isinstance(site_kw.value, ast.Constant) or not isinstance(
        site_kw.value.value, str
    ):
        return ["site= must be a string literal"]
    if site is not None and site_kw.value.value.casefold() != site.casefold():
        return [f"site= is {site_kw.value.value!r}, not {site!r}"]
    return []
