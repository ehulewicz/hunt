from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from personal_os_backend.jobs_models import JobsProfile, JobsProfileUpdate
from personal_os_backend.jobs_store import PROFILE_COLUMNS


def test_autofill_profile_serializes_new_standing_facts_without_rounding() -> None:
    profile = JobsProfile(
        graduation_term="Fall",
        completed_coop_terms=Decimal("4.5"),
        minimum_salary=Decimal("35"),
        minimum_salary_period="hour",
        updated_at=datetime.now(UTC),
    )
    payload = profile.model_dump(mode="json")
    assert payload["graduation_term"] == "Fall"
    assert payload["completed_coop_terms"] == "4.5"
    assert payload["minimum_salary_period"] == "hour"
    assert {"graduation_term", "completed_coop_terms", "minimum_salary_period"} <= set(
        PROFILE_COLUMNS
    )


@pytest.mark.parametrize(
    "update",
    [
        {"graduation_term": "Autumn"},
        {"minimum_salary_period": "week"},
        {"completed_coop_terms": Decimal("-0.5")},
    ],
)
def test_profile_rejects_invalid_standing_facts(update: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        JobsProfileUpdate.model_validate(update)
