"""Routes: how to reach a posting on one site, written as code when the generic
navigator (`jobs/navigate.py`) cannot.

A route is a function `(page, target) -> bool` in a module of this package,
registered for the sites it knows with `@route("nintendo.com")`. It returns
True once it believes the page is the posting; the navigator reads that back
before it counts. The fixer (`jobs/fix/`) adds a route as a new module here,
so adding one never edits another; every module is imported on first lookup.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable

from personal_os_worker.jobs.browser import registrable_site
from personal_os_worker.jobs.navigate import Route

ROUTES: dict[str, Route] = {}
_loaded = False


def route(*sites: str) -> Callable[[Route], Route]:
    """Register a route for each registrable site it names ("nintendo.com")."""

    def register(function: Route) -> Route:
        for site in sites:
            ROUTES[site.casefold()] = function
        return function

    return register


def load() -> None:
    """Import every route module once, so each registers itself."""
    global _loaded
    if _loaded:
        return
    for module in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{module.name}")
    _loaded = True


def route_for(url: str) -> Route | None:
    """The route written for this URL's site, if any."""
    load()
    return ROUTES.get(registrable_site(url))
