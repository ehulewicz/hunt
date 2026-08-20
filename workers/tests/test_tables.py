from __future__ import annotations

import pytest

from personal_os_worker import tables

IDS = [
    "3f2a8c91-1111-4444-8888-aaaaaaaaaaaa",
    "3f2a8c92-2222-4444-8888-bbbbbbbbbbbb",
    "9d4e7b05-3333-4444-8888-cccccccccccc",
]


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(tables, "state_dir", lambda: tmp_path)
    return tmp_path


def test_row_numbers_resolve_against_the_last_listing() -> None:
    tables.remember("listings", IDS)
    assert tables.resolve("listings", "1") == IDS[0]
    assert tables.resolve("listings", "3") == IDS[2]


def test_row_number_out_of_range_says_so() -> None:
    tables.remember("listings", IDS)
    with pytest.raises(tables.ReferenceError, match="out of range"):
        tables.resolve("listings", "4")


def test_row_numbers_need_a_cache() -> None:
    with pytest.raises(tables.ReferenceError, match="No cached"):
        tables.resolve("listings", "1")


def test_unambiguous_prefix_resolves() -> None:
    tables.remember("listings", IDS)
    assert tables.resolve("listings", "9d4e") == IDS[2]


def test_ambiguous_prefix_is_refused_rather_than_guessed() -> None:
    tables.remember("listings", IDS)
    with pytest.raises(tables.ReferenceError, match="matches 2 rows"):
        tables.resolve("listings", "3f2a")


def test_longer_prefix_disambiguates() -> None:
    tables.remember("listings", IDS)
    assert tables.resolve("listings", "3f2a8c92") == IDS[1]


def test_very_short_prefix_is_refused() -> None:
    tables.remember("listings", IDS)
    with pytest.raises(tables.ReferenceError, match="too short"):
        tables.resolve("listings", "3f")


def test_unknown_prefix_points_at_refreshing() -> None:
    tables.remember("listings", IDS)
    with pytest.raises(tables.ReferenceError, match="refresh"):
        tables.resolve("listings", "ffffffff")


def test_listings_and_apps_caches_are_independent() -> None:
    tables.remember("listings", IDS[:1])
    tables.remember("apps", IDS[1:])
    assert tables.resolve("listings", "1") == IDS[0]
    assert tables.resolve("apps", "1") == IDS[1]


def test_short_id_strips_dashes() -> None:
    assert tables.short_id(IDS[0]) == "3f2a8c91"
