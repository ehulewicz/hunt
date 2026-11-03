"""CLI connection settings, terminal rendering, and row references."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console


@dataclass(frozen=True, slots=True)
class CliConfig:
    """Connection settings for the local Jobs CLI.

    The publishable key is public by design and ships in browser bundles, but it
    is kept out of the repository so configuration stays machine-local.
    """

    api_url: str
    supabase_url: str
    supabase_publishable_key: str
    cdp_url: str = "http://127.0.0.1:9333"


def cli_config_path() -> Path:
    configured = os.environ.get("PERSONAL_OS_CLI_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "personal-os" / "cli.json"


def save_cli_config(config: CliConfig) -> Path:
    path = cli_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "api_url": config.api_url,
                "supabase_url": config.supabase_url,
                "supabase_publishable_key": config.supabase_publishable_key,
                "cdp_url": config.cdp_url,
            },
            indent=2,
        )
        + "\n"
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
    return path


def load_cli_config() -> CliConfig:
    path = cli_config_path()
    try:
        data = json.loads(path.read_text())
        return CliConfig(
            api_url=str(data["api_url"]).rstrip("/"),
            supabase_url=str(data["supabase_url"]).rstrip("/"),
            supabase_publishable_key=str(data["supabase_publishable_key"]),
            cdp_url=str(data.get("cdp_url", "http://127.0.0.1:9333")),
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"CLI is not configured. Write {path} with api_url, supabase_url, "
            "and supabase_publishable_key."
        ) from error


SHORT_ID_LENGTH = 8

console = Console()


class ReferenceError(RuntimeError):
    """Raised when a row reference cannot be resolved to exactly one record."""


def short_id(value: str) -> str:
    return value.replace("-", "")[:SHORT_ID_LENGTH]


def pick(items: list[dict[str, Any]], reference: str, *, kind: str) -> dict[str, Any]:
    """The one item a row number (1-based position) or id prefix denotes."""
    if reference.isdigit():
        index = int(reference)
        if not 1 <= index <= len(items):
            raise ReferenceError(f"Row {index} is out of range; there are {len(items)} {kind}.")
        return items[index - 1]
    normalized = reference.replace("-", "").casefold()
    if len(normalized) < 4:
        raise ReferenceError(f"Id prefix {reference!r} is too short; use at least 4 characters.")
    matches = [
        item
        for item in items
        if str(item.get("id", "")).replace("-", "").casefold().startswith(normalized)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ReferenceError(
            f"Id prefix {reference!r} matches {len(matches)} {kind}; use more characters."
        )
    raise ReferenceError(f"No {kind} match {reference!r}.")
