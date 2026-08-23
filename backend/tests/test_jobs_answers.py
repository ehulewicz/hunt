from personal_os.jobs_answers import answer_storage_key, normalize_answer_key
from personal_os.jobs_models import AnswerLibraryCreate, AnswerLibraryUpdate


def test_answer_keys_ignore_case_spacing_and_harmless_punctuation() -> None:
    assert normalize_answer_key("  Why this company?! ") == "why this company"
    assert normalize_answer_key("C++ / C# experience") == "c++ c# experience"
    assert answer_storage_key("why this company") == answer_storage_key("why this company")


def test_answer_reuse_policy_is_explicit_and_validated() -> None:
    exact = AnswerLibraryCreate(
        question="Are you authorized?", answer_text="Yes.", approved=True
    )
    contextual = AnswerLibraryUpdate(reuse_policy="contextual")
    assert exact.reuse_policy == "exact"
    assert contextual.reuse_policy == "contextual"
