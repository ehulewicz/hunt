"""Learned files: the ones a problem may reuse, and reading and editing their source."""

from __future__ import annotations

from pathlib import Path

from personal_os_worker.jobs.fix import lane


def _sites_arg(source: str, site: str) -> str | None:
    """The file with `site` added to its declaration's `sites=` tuple — one
    site per line with a trailing comma, indented under the keyword, so a
    growing tuple stays inside the line limit — or None when that tuple could
    not be edited safely."""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "sites" or not isinstance(keyword.value, ast.Tuple):
                continue
            value = keyword.value
            if not all(
                isinstance(item, ast.Constant) and isinstance(item.value, str)
                for item in value.elts
            ):
                return None
            names = [
                str(item.value) for item in value.elts
                if isinstance(item, ast.Constant)
            ]
            if site.casefold() in {name.casefold() for name in names}:
                return source  # already named
            if value.end_lineno is None or value.end_col_offset is None:
                return None
            indent = " " * (keyword.col_offset + 4)
            closing = " " * keyword.col_offset
            listing = "".join(f'{indent}"{name}",\n' for name in [*names, site])
            lines = source.splitlines(keepends=True)
            start_row, end_row = value.lineno - 1, value.end_lineno - 1
            lines[start_row:end_row + 1] = [
                lines[start_row][:value.col_offset]
                + f"(\n{listing}{closing})"
                + lines[end_row][value.end_col_offset:]
            ]
            return "".join(lines)
    return None


def add_site(source: str, site: str) -> str | None:
    """Public spelling of the sites-edit for callers and tests."""
    return _sites_arg(source, site)


def structure_of(source: str) -> str:
    """The `structure=` literal a source's declaration carries, or ""."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "structure" and isinstance(
                    keyword.value, ast.Constant
                ):
                    return str(keyword.value.value)
    return ""


def _declared_sites_and_fixtures(source: str) -> tuple[set[str], set[str]]:
    """One file's `sites=` strings (casefolded) and `Example(fixture=...)`
    strings, for comparing a revision with the file it revises."""
    import ast

    sites: set[str] = set()
    fixtures: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return sites, fixtures
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "sites" and isinstance(keyword.value, ast.Tuple):
                sites |= {
                    str(item.value).casefold()
                    for item in keyword.value.elts
                    if isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                }
            if (
                keyword.arg == "fixture"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                fixtures.add(keyword.value.value)
    return sites, fixtures


def _revision_dropped(old: str, new: str) -> str | None:
    """What a revision lost: an earlier site or an earlier example's fixture."""
    old_sites, old_fixtures = _declared_sites_and_fixtures(old)
    new_sites, new_fixtures = _declared_sites_and_fixtures(new)
    missing_sites = old_sites - new_sites
    if missing_sites:
        return (
            "a revision keeps every earlier site in sites=; yours dropped "
            + ", ".join(sorted(missing_sites))
        )
    missing_fixtures = old_fixtures - new_fixtures
    if missing_fixtures:
        return (
            "a revision keeps every earlier example; yours dropped the fixture "
            + ", ".join(sorted(missing_fixtures))
        )
    return None


def _same_structure(problem: lane.Problem) -> list[Path]:
    """Learned files of this problem's type with its structure, not yet this site."""
    from personal_os_worker.jobs import registry

    category = problem.category
    if category == "routes":
        return []
    found: list[Path] = []
    for path in registry._learned_files(category):
        try:
            module = registry._import_file(path, category)
        except Exception:
            continue
        for value in vars(module).values():
            decl = registry._categories()[category]
            if not isinstance(value, decl):
                continue
            if problem.site.casefold() in registry._sites_of(value):
                continue  # already lists this site
            if category == "formats":
                if problem.fact_key and problem.fact_key in tuple(
                    getattr(value, "fact_keys", ())
                ):
                    found.append(path)
            elif str(getattr(value, "structure", "")) == problem.structure:
                found.append(path)
    return found


def _kind_candidates(problem: lane.Problem) -> list[Path]:
    """Every learned kind file not naming this site: proven first, then by
    name. For kinds the live probe — not the structure — decides reuse."""
    from personal_os_worker.jobs import registry

    found: list[tuple[bool, str, Path]] = []
    for path in registry._learned_files("kinds"):
        try:
            module = registry._import_file(path, "kinds")
        except Exception:
            continue
        for value in vars(module).values():
            if not isinstance(value, registry._categories()["kinds"]):
                continue
            if problem.site.casefold() in registry._sites_of(value):
                continue
            found.append((not getattr(value, "proven", False), path.stem, path))
    return [path for _proven, _stem, path in sorted(found)]
