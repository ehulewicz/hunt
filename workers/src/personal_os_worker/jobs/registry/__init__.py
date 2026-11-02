"""The registry: what the worker knows how to fill, one layout for built-ins and learned.

Each category holds built-in declarations beside a `learned/` package of a
site's own: `kinds` (control kinds), `strategies` (how a kind is operated),
`formats` (how a fact is written), `questions` (how a group names itself), and
`routes` (how to reach a posting). Declarations live in `registry/api.py`, the
only module a learned file may import.

Built-ins come first: a site's learned declarations are offered only after the
built-ins fail, never ahead of them. `learned()` is imported lazily on first
call — never at `os` startup — and cached; `reload_learned()` clears the cache
so a fix written mid-run is picked up.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# CATEGORIES is resolved lazily: importing api (and so the strategies family)
# at package import would loop back through autofill.
def _categories() -> dict[str, type]:
    from personal_os_worker.jobs.registry.api import (
        Format,
        Kind,
        QuestionReader,
        Route,
        Strategy,
    )

    return {
        "kinds": Kind,
        "strategies": Strategy,
        "formats": Format,
        "questions": QuestionReader,
        "routes": Route,
    }


def __getattr__(name: str) -> Any:
    if name == "CATEGORIES":
        return _categories()
    raise AttributeError(name)

_cache: dict[tuple[str, str], list[Any]] = {}
_loaded_modules: dict[str, list[Any]] = {}
_loaded_paths: dict[str, list[Path]] = {}
_refused: dict[str, list[str]] = {}


def refused() -> dict[str, list[str]]:
    """The learned files the policy rejected, with each violation."""
    return dict(_refused)


def _learned_package(category: str) -> Any:
    return importlib.import_module(f"{__name__}.{category}.learned")


def _learned_files(category: str) -> list[Path]:
    package = _learned_package(category)
    return sorted(
        Path(directory) / f"{module.name}.py"
        for directory in package.__path__
        for module in pkgutil.iter_modules([directory])
        if not module.name.startswith("_")
        and (Path(directory) / f"{module.name}.py").is_file()
    )


def _import_file(path: Path, category: str) -> Any:
    # Always exec the file fresh: two sites' fixes may share a stem, and a fix
    # kept mid-run replaces the module under the same name.
    name = f"{__name__}.{category}.learned.{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _learned_modules(category: str) -> list[tuple[Path, Any]]:
    """Every (file, module) in the learned package that passes policy, in
    filename order."""
    if category in _loaded_modules:
        return list(zip(_loaded_paths[category], _loaded_modules[category], strict=True))
    from personal_os_worker.jobs.registry import policy

    modules, paths = [], []
    for path in _learned_files(category):
        violations = policy.check_file(path, category=category)
        if violations:
            _refused[str(path)] = violations
            logger.warning("learned file %s refused: %s", path, violations)
            continue
        try:
            modules.append(_import_file(path, category))
            paths.append(path)
        except Exception as error:
            _refused[str(path)] = [f"import failed: {error}"]
            logger.warning("learned file %s failed to import: %s", path, error)
    _loaded_modules[category] = modules
    _loaded_paths[category] = paths
    return list(zip(paths, modules, strict=True))


def load_file(path: str | Path, *, category: str) -> list[Any]:
    """Check and import one learned file into the cache without a restart.

    Used for a fix kept mid-run and by `os hunt replay --learned`.
    """
    from personal_os_worker.jobs.registry import policy

    path = Path(path)
    violations = policy.check_file(path, category=category)
    if violations:
        _refused[str(path)] = violations
        raise ValueError(f"{path} refused: {violations}")
    declaration = _categories()[category]
    found = [
        value
        for value in vars(_import_file(path, category)).values()
        if isinstance(value, declaration)
    ]
    from personal_os_worker.jobs.fix import trial

    tracker = trial.current()
    if tracker is not None:
        tracker.note_kept(path)
    for value in found:
        _remember(value, path)
        for site in _sites_of(value):
            key = (category, site)
            _cache.setdefault(key, [])
            if value not in _cache[key]:
                _cache[key].append(value)
    return found


def _sites_of(value: object) -> set[str]:
    """A declaration's sites, casefolded; a lone site= still counts."""
    sites = getattr(value, "sites", ())
    if not isinstance(sites, tuple):
        sites = (getattr(value, "site", ""),)
    return {str(one).casefold() for one in sites}


def learned(category: str, site: str) -> list[Any]:
    """The learned declarations of one category and site, in filename order."""
    key = (category, site.casefold())
    if key not in _cache:
        declaration = _categories()[category]
        found = [
            (path, value)
            for path, module in _learned_modules(category)
            for value in vars(module).values()
            if isinstance(value, declaration)
            and key[1] in _sites_of(value)
        ]
        for path, value in found:
            _remember(value, path)
        _cache[key] = [value for _path, value in found]
    return _cache[key]


def reload_learned() -> None:
    """Forget the cache, so a file written mid-run is seen."""
    _cache.clear()
    _loaded_modules.clear()
    _loaded_paths.clear()
    _refused.clear()
    _decl_source.clear()


def reopen_for_version(state_dir: Any, api: Any, *, ats: str = "") -> int:
    """Reopen every unsolved problem row once when this device's fixer API is
    older than api.VERSION; returns problems reopened."""
    from personal_os_worker.jobs.registry.api import VERSION

    marker = Path(state_dir) / "fixer-api-version"
    previous = 0
    with contextlib.suppress(Exception):
        previous = int(marker.read_text().strip())
    marker.write_text(str(VERSION))
    if previous >= VERSION:
        return 0
    reopened = 0
    try:
        for issue in api.autofill_failures(status="unsolved") or []:
            signature = str(issue.get("signature") or "")
            if "problem|" not in signature:
                continue
            api.update_autofill_failure(issue["id"], {"status": "open"})
            reopened += 1
    except Exception:
        return reopened
    return reopened


import contextlib  # noqa: E402

# ----- trial reporting --------------------------------------------------------

_decl_source: dict[int, Path] = {}


def _remember(value: object, path: Path) -> None:
    """Where a learned declaration came from, for trial bookkeeping."""
    _decl_source[id(value)] = path


def decl_source(value: object) -> Path | None:
    return _decl_source.get(id(value))


def report(value: object, ok: bool) -> None:
    """A declaration's outcome goes to the run's trial tracker, if any."""
    from personal_os_worker.jobs.fix import trial

    tracker = trial.current()
    if tracker is not None:
        tracker.report(value, ok)
