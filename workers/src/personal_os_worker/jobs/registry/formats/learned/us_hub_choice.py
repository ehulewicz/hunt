"""One Greenhouse question asks for a US hub through a menu that lists nothing until opened."""

from __future__ import annotations

import re

from personal_os_worker.jobs.registry.api import Format, FormatInput

# Greenhouse hosts every employer under one site, so the wording is the only gate.
HUB_QUESTION = re.compile(r"\bUS hub\b", re.IGNORECASE)
# Figma's US hubs as remembered, not read from the saved page; the live probe checks them.
HUBS = (("San Francisco", "CA"), ("New York", "NY"))


def hub_from_policy(render_input: FormatInput) -> str | None:
    """The hub the office policy prefers, as a bare city the opened menu's wording will match.

    A tier 1 city in the policy's own order, else a tier 2 state. With options in
    hand the built-in office choice has already answered, so this stays out of it.
    """
    strategy = render_input.value
    if render_input.options or not isinstance(strategy, dict):
        return None
    if HUB_QUESTION.search(render_input.question) is None:
        return None
    for wanted in strategy.get("tier_1_preferred_cities") or []:
        if not isinstance(wanted, dict):
            continue
        for city, state in HUBS:
            named = str(wanted.get("city", "")).casefold() == city.casefold()
            if named and str(wanted.get("state", "")).upper() == state:
                return city
    fallbacks = {str(state).upper() for state in strategy.get("tier_2_state_fallbacks") or []}
    for city, state in HUBS:
        if state in fallbacks:
            return city
    return None


QUESTION = "From which US hub do you prefer to work?"
CHICAGO = {"city": "Chicago", "state": "IL"}
NEW_YORK = {"city": "New York", "state": "NY"}
SAN_FRANCISCO = {"city": "San Francisco", "state": "CA"}


def policy_input(
    cities: list[dict[str, str]],
    states: list[str],
    *,
    options: tuple[str, ...] = (),
    question: str = QUESTION,
) -> FormatInput:
    return FormatInput(
        value={"tier_1_preferred_cities": cities, "tier_2_state_fallbacks": states},
        value_type="object",
        options=options,
        question=question,
        input_type="text",
    )


FORMAT = Format(
    name="us_hub_choice",
    fact_keys=("preferences.location_strategy",),
    render=hub_from_policy,
    sites=(
        "greenhouse.io",
        "microsoft.com",
    ),
    proven=False,
    examples=(
        # The first tier 1 city that is a hub, in the policy's order.
        (policy_input([CHICAGO, SAN_FRANCISCO, NEW_YORK], ["IL"]), "San Francisco"),
        (policy_input([CHICAGO, NEW_YORK, SAN_FRANCISCO], ["IL"]), "New York"),
        # No tier 1 hub: a tier 2 state's hub.
        (policy_input([CHICAGO], ["IL", "CA"]), "San Francisco"),
        # The policy names no hub: no guess.
        (policy_input([CHICAGO], ["IL"]), None),
        # Options in hand are the built-in's; other questions are not this one.
        (policy_input([NEW_YORK], [], options=("New York, NY",)), None),
        (policy_input([NEW_YORK], [], question="Which office do you prefer?"), None),
    ),
)
