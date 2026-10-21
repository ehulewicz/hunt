from personal_os_worker.jobs.provenance import spans


def test_previous_writing_wins_over_a_model_that_copied_it() -> None:
    previous = "I built the deployment pipeline."
    draft = previous + " I also owned the production rollout."
    assert spans(draft, draft=draft, previous=[previous]) == [
        {"start": 0, "end": 32, "source": "previous"},
        {"start": 32, "end": 69, "source": "model"},
    ]


def test_manual_edits_are_unmarked_and_offsets_follow_the_final_text() -> None:
    shared = "I built the deployment pipeline."
    final = "My own introduction. " + shared + " My own conclusion."
    assert spans(final, draft=shared) == [{"start": 21, "end": 53, "source": "model"}]


def test_empty_and_coincidental_matches_have_no_attribution() -> None:
    assert spans("", draft="A substantial draft that was removed.") == []
    assert spans("My own writing.") == []
    assert spans("I enjoy coding.", draft="I enjoy coding.") == []


def test_unicode_offsets_are_character_positions_and_sources_do_not_overlap() -> None:
    text = "🚀 Déploying systems taught me ownership."
    assert spans(text, draft=text, previous=[text, text]) == [
        {"start": 0, "end": 40, "source": "previous"},
    ]
