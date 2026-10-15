from personal_os_worker.jobs.sites import detect_ats, failure_signature, option_match


def test_the_ats_is_a_label_read_from_the_host_and_never_a_gate() -> None:
    assert detect_ats("https://boards.greenhouse.io/acme/jobs/1") == "greenhouse"
    assert detect_ats("https://jobs.ashbyhq.com/acme/1") == "ashby"
    assert detect_ats("https://jobs.lever.co/acme/1") == "lever"
    assert detect_ats("https://big.wd5.myworkdayjobs.com/en-US/careers/job/1") == "workday"
    assert detect_ats("https://example.com/jobs/1") == "generic"


def test_choice_matching_requires_one_clear_option() -> None:
    assert option_match(["Yes", "No"], "yes") == "Yes"
    assert option_match(["United States", "United States Minor Outlying Islands"], "United") is None


def test_choice_matching_never_selects_a_non_answer_option() -> None:
    options = ["Male", "Female", "Prefer not to answer"]
    assert option_match(options, "Prefer not to answer") is None
    assert option_match(["Decline To Self Identify"], "Decline") is None


def test_failure_signatures_are_stable_and_category_scoped() -> None:
    first = failure_signature("ashby", "readback_mismatch", "input|email")
    assert first == failure_signature("ashby", "readback_mismatch", "input|email")
    assert first != failure_signature("ashby", "fill_exception", "input|email")
