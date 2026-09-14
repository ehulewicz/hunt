from personal_os_worker.adapters import detect_ats, failure_signature, option_match


def test_supported_ats_detection_is_host_based() -> None:
    assert detect_ats("https://boards.greenhouse.io/acme/jobs/1").name == "greenhouse"
    assert detect_ats("https://jobs.ashbyhq.com/acme/1").name == "ashby"
    assert detect_ats("https://jobs.lever.co/acme/1").name == "lever"
    workday = detect_ats("https://big.wd5.myworkdayjobs.com/en-US/careers/job/1")
    assert workday.name == "workday" and workday.supported is False
    generic = detect_ats("https://example.com/jobs/1")
    assert generic.name == "generic" and generic.supported is False
    assert detect_ats("https://jobs.lever.co/acme/1").supported is True


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
