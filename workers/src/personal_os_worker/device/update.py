"""Keep this device's checkout current, without ever touching work in progress.

Only a clean checkout of `main` that is behind `origin/main` changes: it
fast-forwards and resyncs its environment. A checkout with edits, commits of its
own, or another branch is never stashed, reset, or merged; it runs as it is and
says "running local changes at <commit>". Untracked files are not changes:
nothing here would overwrite them, and a fast-forward that would refuses.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Run = Callable[..., subprocess.CompletedProcess[str]]

# A dependency change needs the installed tools rebuilt; a source change does
# not, because they are editable installs of this checkout.
DEPENDENCY_FILES = ("uv.lock", "workers/pyproject.toml", "backend/pyproject.toml")
TOOLS = {"personal-os-worker": "workers", "personal-os-backend": "backend"}


@dataclass(frozen=True, slots=True)
class Checkout:
    commit: str
    branch: str
    dirty: bool
    ahead: int
    behind: int

    @property
    def local_changes(self) -> bool:
        return self.dirty or self.ahead > 0 or self.branch != "main"

    @property
    def short(self) -> str:
        return self.commit[:7]


@dataclass(frozen=True, slots=True)
class Update:
    checkout: Checkout
    pulled_from: str | None  # the commit before a fast-forward, when there was one
    note: str


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def source_fingerprint(root: Path) -> str:
    """A digest of a source tree, so a pull or an edit is visible to a running process."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        stat = path.stat()
        digest.update(f"{path.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def _git(run: Run, repo: Path, *arguments: str, timeout: float = 30) -> str:
    result = run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(_last_line(result.stderr) or f"git {arguments[0]} failed")
    return result.stdout


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def read_checkout(repo: Path, run: Run = subprocess.run) -> Checkout:
    """Where this checkout stands against the last fetched origin/main."""
    commit = _git(run, repo, "rev-parse", "HEAD").strip()
    branch = _git(run, repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    dirty = bool(_git(run, repo, "status", "--porcelain", "--untracked-files=no").strip())
    try:
        counts = _git(run, repo, "rev-list", "--left-right", "--count", "HEAD...origin/main")
        ahead_text, behind_text = counts.split()
        ahead, behind = int(ahead_text), int(behind_text)
    except (RuntimeError, ValueError):
        ahead, behind = 0, 0
    return Checkout(commit=commit, branch=branch, dirty=dirty, ahead=ahead, behind=behind)


def _fetch(repo: Path, run: Run) -> str | None:
    """None once origin/main is fresh, else why it is not."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    # The agent runs unattended: a passphrase prompt would hang it forever.
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")
    try:
        result = run(
            ["git", "-C", str(repo), "fetch", "--quiet", "origin", "main"],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return error.__class__.__name__
    return None if result.returncode == 0 else _last_line(result.stderr) or "git fetch failed"


def _sync_environment(repo: Path, previous: str, run: Run) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH, so the environment could not be synced")
    run(
        [uv, "sync", "--all-packages", "--python", "3.13"],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(repo),
    )
    changed = _git(run, repo, "diff", "--name-only", previous, "HEAD", "--", *DEPENDENCY_FILES)
    if not changed.strip():
        return
    listed = run(
        [uv, "tool", "list"], check=False, capture_output=True, text=True, timeout=60
    ).stdout
    installed = {line.split()[0] for line in listed.splitlines() if line and not line[0].isspace()}
    for tool, directory in TOOLS.items():
        if tool in installed:
            run(
                [uv, "tool", "install", "--reinstall", "--python", "3.13", "--editable",
                 str(repo / directory)],
                check=True,
                capture_output=True,
                text=True,
                timeout=600,
            )


def update_checkout(repo: Path, run: Run = subprocess.run) -> Update:
    """Fast-forward a clean checkout that is behind; leave every other one as it is."""
    unreachable = _fetch(repo, run)
    checkout = read_checkout(repo, run)
    if checkout.local_changes:
        return Update(checkout, None, f"running local changes at {checkout.short}")
    if unreachable is not None:
        note = f"could not reach origin ({unreachable}); at {checkout.short}"
        return Update(checkout, None, note)
    if checkout.behind == 0:
        return Update(checkout, None, f"up to date at {checkout.short}")
    previous = checkout.commit
    try:
        _git(run, repo, "merge", "--ff-only", "--quiet", "origin/main", timeout=120)
        _sync_environment(repo, previous, run)
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        current = read_checkout(repo, run)
        pulled = previous if current.commit != previous else None
        return Update(current, pulled, f"update stopped: {_last_line(str(error)) or error}")
    current = read_checkout(repo, run)
    return Update(current, previous, f"updated {previous[:7]} to {current.short}")
