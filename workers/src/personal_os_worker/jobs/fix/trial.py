"""Trial bookkeeping: a learned declaration proves itself or is retired.

One Trial lives for one run (the session owns it and attaches it to the
registry). A declaration loaded at run start with proven=False that works on
a run it was not kept in is marked proven by a one-line commit; one that
fails on trial is retired — its last commit reverted, or the new file and its
fixtures deleted — and the run stops using it. A file kept or revised this
run is never proven or retired by it.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

from personal_os_worker.jobs.fix import keep


def _rel(path: Path) -> tuple[str, str]:
    """(learned_rel, fixture_rel) for a file under <category>/learned/."""
    category = path.parent.parent.name
    return keep.learned_rel(category, path.stem), keep.fixture_rel(
        category, path.stem
    )


class Trial:
    """The run's proven flips and retirements; attached to the registry so the
    capture/fill/route seams can report outcomes."""

    def __init__(
        self,
        *,
        repo: Path,
        api: Any,
        run_id: str = "",
        marker: Any = keep.mark_proven,
        retirer: Any = keep.retire,
    ) -> None:
        self.repo = repo
        self.api = api
        self.run_id = run_id
        self._mark = marker
        self._retire = retirer
        # Stems kept or revised in this run: they are never proven or retired here.
        self.kept_this_run: set[str] = set()
        # Stems already resolved (proven or retired) this run.
        self._resolved: set[str] = set()

    def note_kept(self, path: Path) -> None:
        self.kept_this_run.add(path.stem)

    def report(self, declaration: Any, ok: bool) -> bool:
        """One declaration's outcome; returns True when a proven/retire commit ran."""
        if getattr(declaration, "proven", True):
            return False  # proven files are never touched
        from personal_os_worker.jobs import registry

        path = registry.decl_source(declaration)
        if path is None or path.stem in self.kept_this_run:
            return False
        if path.stem in self._resolved:
            return False
        self._resolved.add(path.stem)
        learned_rel, fixture_rel = _rel(path)
        try:
            if ok:
                kept = self._mark(self.repo, learned_rel)
            else:
                kept = self._retire(
                    self.repo, learned_rel, fixture_rel,
                    was_new=self._was_new(learned_rel),
                )
        except Exception as error:  # a git/api failure never ends the run
            with suppress(Exception):
                self.api.report(f"trial record failed for {path.stem}: {error}")
            return False
        with suppress(Exception):
            self.api.record_code_change(
                kept.commit, origin="worker", parent=kept.parent,
                detail={
                    "cause": "trial",
                    "outcome": "proven" if ok else "retired",
                    "file": learned_rel,
                    "run_id": self.run_id,
                    "pushed": kept.pushed,
                },
            )
        if not ok:
            registry.reload_learned()
        return True

    def _was_new(self, learned_rel: str) -> bool:
        """Whether the file's last commit added it (no earlier version to restore)."""
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(self.repo), "cat-file", "-e", f"HEAD~1:{learned_rel}"],
            capture_output=True,
        )
        return result.returncode != 0


_trial: Trial | None = None


def attach(trial: Trial | None) -> None:
    """The run wires its tracker in at start; cleared when the run ends."""
    global _trial
    _trial = trial


def current() -> Trial | None:
    return _trial


def revert_retired(api: Any, repo: Path) -> int:
    """The online device applies the user's reverts: every retired fix whose
    file is still in this checkout loses its file and fixtures in one commit on
    main. Skipped when those paths have local edits."""
    import subprocess

    from personal_os_worker.jobs import registry

    try:
        report = api.learned()
    except Exception:
        return 0
    retired = [
        str(fix.get("file") or "")
        for fix in report.get("fixes", [])
        if fix.get("status") == "retired"
    ]
    if not retired:
        return 0
    reverted = 0
    for learned_rel in retired:
        path = repo / learned_rel
        if not path.exists():
            continue
        fixture_rel = keep.fixture_rel(path.parent.parent.name, path.stem)
        if not _unchanged(repo, learned_rel, fixture_rel):
            continue  # local edits on those paths: leave them alone
        path.unlink(missing_ok=True)
        fixtures = repo / fixture_rel
        if fixtures.is_dir():
            for file in fixtures.iterdir():
                file.unlink()
            fixtures.rmdir()
        with suppress(Exception):
            _commit_removal(repo, learned_rel, fixture_rel)
            subprocess.run(
                ["git", "-C", str(repo), "push", "-q", "origin",
                 "HEAD:refs/heads/main"],
                capture_output=True, timeout=30,
            )
        registry.retire_files([learned_rel])
        reverted += 1
    # Any retired file still on disk (or the loader has seen it) is skipped too.
    registry.retire_files(retired)
    return reverted


def _commit_removal(repo: Path, learned_rel: str, fixture_rel: str) -> None:
    """One commit on main, through keep.py's touched-paths commit, removing the
    file and its fixtures."""
    keep._commit_touched(
        repo,
        f"Revert {learned_rel}\n\nThe fix was retired in the Inbox.",
        staged=[], removed=[learned_rel, fixture_rel], push=False,
    )


def _unchanged(repo: Path, learned_rel: str, fixture_rel: str) -> bool:
    """Every byte of the file and its fixtures still matches HEAD."""
    import subprocess

    paths = [repo / learned_rel]
    if (repo / fixture_rel).is_dir():
        paths += sorted((repo / fixture_rel).glob("*"))
    for path in paths:
        rel = path.relative_to(repo)
        head = subprocess.run(
            ["git", "-C", str(repo), "show", f"HEAD:{rel}"],
            capture_output=True,
        )
        if head.returncode != 0 or head.stdout != path.read_bytes():
            return False
    return True
