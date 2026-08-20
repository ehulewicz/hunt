from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    api_url: str
    token: str
    cdp_url: str = "http://127.0.0.1:9333"


def config_path() -> Path:
    configured = os.environ.get("PERSONAL_OS_WORKER_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "personal-os" / "worker.json"


def save_config(config: WorkerConfig) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"api_url": config.api_url, "token": config.token, "cdp_url": config.cdp_url},
            indent=2,
        )
        + "\n"
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
    return path


def load_config() -> WorkerConfig:
    path = config_path()
    try:
        data = json.loads(path.read_text())
        return WorkerConfig(
            api_url=str(data["api_url"]).rstrip("/"),
            token=str(data["token"]),
            cdp_url=str(data.get("cdp_url", "http://127.0.0.1:9333")),
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("Worker is not paired. Run personal-os-worker pair first.") from error


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
