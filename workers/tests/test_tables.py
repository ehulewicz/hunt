from __future__ import annotations

import pytest

from personal_os_worker.tables import ReferenceError, pick

ITEMS = [
    {"id": "3f2a8c91-1111-4444-8888-aaaaaaaaaaaa", "title": "first"},
    {"id": "3f2a8c92-2222-4444-8888-bbbbbbbbbbbb", "title": "second"},
    {"id": "9d4e7b05-3333-4444-8888-cccccccccccc", "title": "third"},
]


def test_a_row_number_is_the_position_in_the_list_just_fetched() -> None:
    assert pick(ITEMS, "1", kind="rows")["title"] == "first"
    assert pick(ITEMS, "3", kind="rows")["title"] == "third"


def test_row_number_out_of_range_says_so() -> None:
    with pytest.raises(ReferenceError, match="out of range; there are 3 rows"):
        pick(ITEMS, "4", kind="rows")
    with pytest.raises(ReferenceError):
        pick([], "1", kind="rows")


def test_an_id_prefix_resolves_when_unambiguous() -> None:
    assert pick(ITEMS, "9d4e", kind="rows")["title"] == "third"
    assert pick(ITEMS, "3f2a-8c92", kind="rows")["title"] == "second"
    assert pick(ITEMS, "3F2A8C91", kind="rows")["title"] == "first"


def test_a_short_or_shared_prefix_is_refused() -> None:
    with pytest.raises(ReferenceError, match="too short"):
        pick(ITEMS, "3f", kind="rows")
    with pytest.raises(ReferenceError, match="matches 2 rows"):
        pick(ITEMS, "3f2a", kind="rows")
    with pytest.raises(ReferenceError, match="No rows match"):
        pick(ITEMS, "ffffffff", kind="rows")
