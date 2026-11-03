"""The computed-fact list agrees with the catalog and with the backend's copy.

The backend cannot import the worker, so `jobs_facts_computed.py` mirrors the
worker's declarations as a pure literal this test reads with `ast`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from personal_os_worker.jobs.facts.catalog import CATALOG
from personal_os_worker.jobs.facts.derived import (
    CAREER_FACTS,
    COMPUTED_FACTS,
    DERIVED_FACTS,
    POSTING_FACTS,
)

BACKEND_COMPUTED = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "personal_os_backend"
    / "jobs_facts_computed.py"
)


def test_computed_facts_match_the_catalog_and_are_declared_once() -> None:
    computed = {key: source for key, (source, _item) in COMPUTED_FACTS.items()}
    catalog = {key: entry.source for key, entry in CATALOG.items() if entry.source != "stored"}
    assert set(computed) == set(catalog)
    for key, source in catalog.items():
        if source == "career":
            assert computed[key] == "career", key
        else:
            assert computed[key] in {"derived", "posting"}, key
    assert len(COMPUTED_FACTS) == len(CAREER_FACTS) + len(DERIVED_FACTS) + len(POSTING_FACTS)


def _backend_computed() -> dict[str, Any]:
    tree = ast.parse(BACKEND_COMPUTED.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
            if isinstance(target, ast.Name) and target.id == "COMPUTED_FACTS":
                return ast.literal_eval(node.value)  # type: ignore[no-any-return]
    raise AssertionError(f"no COMPUTED_FACTS assignment in {BACKEND_COMPUTED}")


def test_backend_computed_facts_match_the_worker() -> None:
    expected = {
        key: (source, CATALOG[key].label, item.reads)
        for key, (source, item) in COMPUTED_FACTS.items()
    }
    assert _backend_computed() == expected
