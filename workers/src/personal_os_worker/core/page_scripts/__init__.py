"""Page scripts: JavaScript the browser evaluates, kept as package data."""

import functools
import importlib.resources


@functools.cache
def load(name: str) -> str:
    """One page script's text, read from <name>.js beside this module."""
    return importlib.resources.files(__package__).joinpath(
        f"{name}.js"
    ).read_text(encoding="utf-8")
