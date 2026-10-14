"""Office selection (design §9): every tier and multi-location shape, with a fixed seed."""

from __future__ import annotations

import pytest

from personal_os_worker.jobs.office import choose_offices, limit_of, parse

STRATEGY = {
    "tier_1_preferred_cities": [
        {"city": "Chicago", "state": "IL"},
        {"city": "Atlanta", "state": "GA"},
        {"city": "New York", "state": "NY"},
        {"city": "San Francisco", "state": "CA"},
        {"city": "Seattle", "state": "WA"},
    ],
    "tier_2_state_fallbacks": ["IL", "GA", "NY", "CA"],
    "tier_3_region_fallbacks": {
        "preferred_region": "US-East",
        "acceptable_regions": ["US-Midwest", "US-East", "US-West"],
    },
}
SEED = "application-7"


def pick(options: list[str], **kwargs: object) -> list[str]:
    return choose_offices(options, STRATEGY, seed=SEED, **kwargs)  # type: ignore[arg-type]


def test_options_are_read_as_us_places() -> None:
    assert (parse("New York, NY").city, parse("New York, NY").state) == ("New York", "NY")
    assert parse("NYC").state == "NY" and parse("SF Bay Area").city == "San Francisco"
    assert parse("Austin, Texas").state == "TX"
    assert parse("Remote - US").remote is True
    assert parse("London, UK").state is None  # unparseable is treated as non-US


def test_tier_one_city_wins_and_the_draw_is_seeded() -> None:
    options = ["Austin, TX", "Chicago, IL", "Boston, MA", "Atlanta, GA"]
    first = pick(options)
    assert first in (["Chicago, IL"], ["Atlanta, GA"])
    assert pick(options) == first  # same application, same draw
    draws = {tuple(choose_offices(options, STRATEGY, seed=str(n))) for n in range(20)}
    assert draws == {("Chicago, IL",), ("Atlanta, GA",)}  # different applications vary


def test_tier_two_state_then_tier_three_region_preferred_first() -> None:
    assert pick(["Austin, TX", "Evanston, IL"]) == ["Evanston, IL"]
    assert pick(["Austin, TX", "Denver, CO", "Boston, MA"]) == ["Boston, MA"]
    assert pick(["Denver, CO", "Austin, TX"]) == ["Denver, CO"]


def test_texas_and_washington_outside_seattle_sit_outside_the_tiers() -> None:
    # R17: Texas reaches only the last resort; R18: Redmond is tier 3, not tier 2.
    assert pick(["Austin, TX", "Redmond, WA"]) == ["Redmond, WA"]
    assert pick(["Seattle, WA", "Redmond, WA"]) == ["Seattle, WA"]
    assert pick(["Austin, TX", "Dallas, TX"], required=False) == []


def test_remote_comes_after_the_tiers_and_before_the_last_resort() -> None:
    assert pick(["Austin, TX", "Remote"]) == ["Remote"]
    assert pick(["Remote", "Denver, CO"]) == ["Denver, CO"]


def test_last_resort_prefers_eastern_time_then_the_first_option() -> None:
    assert pick(["Austin, TX", "Nashville, TN"]) == ["Nashville, TN"]
    assert pick(["Austin, TX", "London, UK"]) == ["Austin, TX"]


def test_select_all_takes_every_tier_one_and_two_match() -> None:
    options = ["Chicago, IL", "Evanston, IL", "Austin, TX", "New York, NY", "Boston, MA"]
    chosen = pick(options, multiple=True, question="Select all offices you would consider")
    assert sorted(chosen) == ["Chicago, IL", "Evanston, IL", "New York, NY"]
    # Tier 1 comes before tier 2.
    assert chosen.index("Evanston, IL") == 2


@pytest.mark.parametrize(
    ("question", "limit"),
    [("Select up to 2 locations", 2), ("Rank your top three offices", 3), ("Any office?", None)],
)
def test_a_limit_is_read_from_the_question(question: str, limit: int | None) -> None:
    assert limit_of(question) == limit


def test_up_to_k_fills_slots_with_tier_one_then_tier_two() -> None:
    options = ["Evanston, IL", "Chicago, IL", "Oakland, CA", "Atlanta, GA"]
    chosen = pick(options, multiple=True, question="Select up to 3 locations")
    assert len(chosen) == 3
    assert set(chosen[:2]) == {"Chicago, IL", "Atlanta, GA"}
    assert chosen[2] in {"Evanston, IL", "Oakland, CA"}


def test_no_tier_match_in_a_multi_question_falls_back_to_a_single_choice() -> None:
    assert pick(["Denver, CO", "Austin, TX"], multiple=True, question="Select all") == [
        "Denver, CO"
    ]
