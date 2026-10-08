from personal_os_worker.jobs.autofill import AnswerResolution, AnswerResolver, build_autofill_plan


def test_resolver_keeps_profile_and_priority_precedence() -> None:
    resolver = AnswerResolver(
        profile={"city": "Atlanta"},
        approved_answers=[{"id": "old", "question": "Salary", "answer_text": "20"}],
        priority=lambda field: AnswerResolution("35", "pattern", "builtin:salary")
        if field["label"] == "Salary" else None,
    )
    assert resolver.resolve({"label": "City"}) == AnswerResolution(
        "Atlanta", "jobs_profile", "profile:city"
    )
    assert resolver.resolve({"label": "Salary"}) == AnswerResolution(
        "35", "pattern", "builtin:salary"
    )


def test_resolver_retains_static_and_dynamic_identity() -> None:
    profile = {"city": "Atlanta"}
    resolver = AnswerResolver(
        profile=profile,
        approved_answers=[
            {"id": "approved", "question": "Favorite tool?", "answer_text": "Python"}
        ],
        fallback=lambda question: AnswerResolution(profile["city"], "pattern", "builtin:location"),
    )
    assert resolver.resolve({"label": "Favorite tool?"}) == AnswerResolution(
        "Python", "answer_library", "library:approved"
    )
    profile["city"] = "Rochester"
    assert resolver.resolve({"label": "Current location?"}) == AnswerResolution(
        "Rochester", "pattern", "builtin:location"
    )


def test_plan_preserves_resolution_before_choice_mapping() -> None:
    plan = build_autofill_plan(
        [{"index": 0, "label": "Yes, authorized", "group": "Eligible?", "type": "radio"},
         {"index": 1, "label": "No", "group": "Eligible?", "type": "radio"}],
        profile={}, approved_answers=[{"id": "a", "question": "Eligible?", "answer_text": "Yes"}],
    )
    assert len(plan.fills) == 1
    assert plan.fills[0].index == 0
    assert plan.fills[0].value == "Yes"
    assert plan.fills[0].resolution == AnswerResolution("Yes", "answer_library", "library:a")
