"""The learned loader: site scoping, filename order, lazy import, reload."""

from __future__ import annotations

import personal_os_worker.jobs.registry as registry


def test_the_categories_cover_the_registry() -> None:
    assert set(registry.CATEGORIES) == {
        "kinds", "strategies", "formats", "questions", "routes",
    }


def test_a_site_with_no_learned_files_finds_none() -> None:
    registry.reload_learned()
    assert registry.learned("kinds", "nowhere.example") == []
    assert registry.learned("routes", "nowhere.example") == []
