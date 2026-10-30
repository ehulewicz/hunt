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
import pkgutil
from typing import Any


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


def _learned_modules(category: str) -> list[Any]:
    """Every module in the category's learned package, in filename order."""
    if category in _loaded_modules:
        return _loaded_modules[category]
    package = importlib.import_module(
        f"{__name__}.{category}.learned"
    )
    modules = [
        importlib.import_module(f"{package.__name__}.{module.name}")
        for module in pkgutil.iter_modules(package.__path__)
        if not module.name.startswith("_")
    ]
    _loaded_modules[category] = modules
    return modules


def learned(category: str, site: str) -> list[Any]:
    """The learned declarations of one category and site, in filename order."""
    key = (category, site.casefold())
    if key not in _cache:
        declaration = _categories()[category]
        _cache[key] = [
            value
            for module in _learned_modules(category)
            for value in vars(module).values()
            if isinstance(value, declaration)
            and str(getattr(value, "site", "")).casefold() == key[1]
        ]
    return _cache[key]


def reload_learned() -> None:
    """Forget the cache, so a file written mid-run is seen."""
    _cache.clear()
    _loaded_modules.clear()
