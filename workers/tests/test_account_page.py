"""The account wall: recognized as its own page, and never mistaken for a form."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.account_page import (
    click_account_control,
    fill_code,
    fill_credentials,
    inspect_account,
    tick_account_terms,
)
from personal_os_worker.jobs.browser import inspect_fields
from personal_os_worker.jobs.page_state import ApplicationPageState, classify_application_page

ACCOUNT = Path(__file__).parent / "fixtures" / "account"


def fixture(name: str) -> str:
    return (ACCOUNT / name).read_text()


@pytest.fixture(scope="module")
def browser():  # type: ignore[no-untyped-def]
    with sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(headless=True)
        except Error as error:
            pytest.skip(f"browser launch is restricted: {error}")
        yield launched
        launched.close()


@pytest.fixture
def page(browser):  # type: ignore[no-untyped-def]
    context = browser.new_context()
    opened = context.new_page()
    yield opened
    context.close()


def show_error(page, message: str) -> None:  # type: ignore[no-untyped-def]
    page.evaluate(
        """message => {
          const alert = document.createElement('div');
          alert.setAttribute('role', 'alert');
          alert.setAttribute('data-automation-id', 'errorMessage');
          alert.textContent = message;
          document.querySelector('#errors').appendChild(alert);
        }""",
        message,
    )


def test_workday_sign_in(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_sign_in.html"))

    seen = inspect_account(page)

    assert seen is not None
    assert seen.view == "sign_in"
    assert seen.google is False
    assert seen.error is None
    assert seen.captcha is False


def test_workday_create(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_create.html"))

    seen = inspect_account(page)

    assert seen is not None and seen.view == "create"


def test_workday_code(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_code.html"))

    seen = inspect_account(page)

    assert seen is not None and seen.view == "code"


def test_check_email(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        "<h2>Check your email</h2><p>We've sent a verification link to e@x.com. "
        "Click it to verify your account.</p>"
    )

    seen = inspect_account(page)

    assert seen is not None and seen.view == "check_email"


def test_forgot_password_page(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        "<h2>Forgot your password?</h2><label for=e>Email Address</label>"
        "<input id=e data-automation-id=email type=text><button>Submit</button>"
    )

    seen = inspect_account(page)

    assert seen is not None and seen.view == "forgot"


def test_new_password_page(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        "<h2>Reset Password</h2><label for=p>New Password</label><input id=p type=password>"
        "<label for=c>Confirm New Password</label><input id=c type=password>"
        "<button>Reset Password</button>"
    )

    seen = inspect_account(page)

    assert seen is not None and seen.view == "new_password"


def test_no_account_error(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_sign_in.html"))
    show_error(page, "We couldn't find an account with that email address.")

    seen = inspect_account(page)

    assert seen is not None and seen.error == "no_account"
    assert "couldn't find an account" in seen.error_text


def test_wrong_password_error(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_sign_in.html"))
    show_error(page, "Wrong email address or password. Please try again.")

    seen = inspect_account(page)

    assert seen is not None and seen.error == "wrong_password"


def test_password_rule_error(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_create.html"))
    show_error(page, "Password must contain at least one special character.")

    seen = inspect_account(page)

    assert seen is not None and seen.error == "password_rule"
    assert seen.error_text == "Password must contain at least one special character."


def test_exists_error(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_create.html"))
    show_error(page, "An account with this email address already exists.")

    seen = inspect_account(page)

    assert seen is not None and seen.error == "exists"


def test_google_control_detected(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        fixture("workday_sign_in.html").replace(
            "</form>", "</form><button type=button>Continue with Google</button>"
        )
    )

    seen = inspect_account(page)

    assert seen is not None and seen.google is True


def test_captcha_widget_on_account_page(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        fixture("workday_sign_in.html").replace(
            "</form>",
            '</form><iframe title="reCAPTCHA" width="304" height="78" '
            'src="data:text/html,recaptcha/api2/anchor?k=x"></iframe>',
        )
    )

    seen = inspect_account(page)

    assert seen is not None and seen.captcha is True


def test_real_form_stays_form(page) -> None:  # type: ignore[no-untyped-def]
    for name in ("greenhouse_apply_with_google.html", "form_with_save_password.html"):
        page.set_content(fixture(name))

        assert (
            classify_application_page(page, inspect_fields(page)) == ApplicationPageState.FORM
        ), name


def test_sign_in_classifies_account(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_sign_in.html"))

    assert classify_application_page(page, inspect_fields(page)) == ApplicationPageState.ACCOUNT


def test_posting_page_is_not_an_account_page(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        "<header><a href='/login'>Sign In</a></header><h1>Software Engineering Intern</h1>"
        "<p>Build things.</p><button>Apply</button>"
    )

    assert inspect_account(page) is None


def test_job_alert_signup_is_not_an_account_page(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        "<h1>Software Engineering Intern</h1><p>Build things.</p><button>Apply</button>"
        "<section><h3>Get job alerts</h3><label for=a>Email</label>"
        "<input id=a type=email><button>Sign up</button></section>"
    )

    assert inspect_account(page) is None


def record_clicks(page) -> None:  # type: ignore[no-untyped-def]
    page.evaluate(
        """() => {
          window.clicked = [];
          document.addEventListener('click', event => {
            const target = event.target.closest('[data-automation-id], button, a');
            if (target) window.clicked.push(
              target.getAttribute('data-automation-id') || target.textContent.trim()
            );
          }, true);
        }"""
    )


def test_fill_and_submit_sign_in(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_sign_in.html"))

    fill_credentials(page, email="e@x.com", password="pw")

    assert click_account_control(page, "submit") is True
    assert page.evaluate("window.sent") == {"email": "e@x.com", "password": "pw"}


def test_fills_confirm_password(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_create.html"))

    fill_credentials(page, email="e@x.com", password="pw")

    assert page.input_value("#email") == "e@x.com"
    assert page.input_value("#password") == "pw"
    assert page.input_value("#verify") == "pw"


def test_submit_on_create_is_create_not_sign_in(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_create.html"))
    record_clicks(page)

    assert click_account_control(page, "submit") is True
    assert page.evaluate("window.clicked") == ["click_filter"]
    assert page.evaluate(
        "document.querySelector('[data-automation-id=click_filter]').getAttribute('aria-label')"
    ) == "Create Account"


def test_opens_create_and_forgot(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_sign_in.html"))
    record_clicks(page)

    assert click_account_control(page, "create") is True
    assert click_account_control(page, "forgot") is True
    assert page.evaluate("window.clicked") == ["createAccountLink", "forgotPasswordLink"]


def test_google_control_is_clicked_by_its_words(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        fixture("workday_sign_in.html").replace(
            "</form>", "</form><button type=button>Continue with Google</button>"
        )
    )
    record_clicks(page)

    assert click_account_control(page, "google") is True
    assert page.evaluate("window.clicked") == ["Continue with Google"]


def test_ticks_only_terms(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_create.html"))

    assert tick_account_terms(page) == 1
    assert page.is_checked("#terms") is True
    assert page.is_checked("#marketing") is False


def test_agreeing_to_marketing_is_not_terms(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        fixture("workday_create.html").replace(
            "Send me emails about new jobs and career events",
            "I agree to receive marketing emails and job alerts",
        )
    )

    assert tick_account_terms(page) == 1
    assert page.is_checked("#marketing") is False


def test_never_clicks_application_submit(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        "<form onsubmit='return false'><label for=e>Email</label><input id=e type=email>"
        "<label for=p>Password</label><input id=p type=password>"
        "<button id=go onclick='window.submitted = true'>Submit application</button></form>"
    )

    assert click_account_control(page, "submit") is False
    assert page.evaluate("window.submitted === undefined") is True


def test_forgot_page_bare_submit_is_the_account_button(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        "<h2>Forgot your password?</h2><form onsubmit='return false'>"
        "<label for=e>Email Address</label><input id=e data-automation-id=email type=text>"
        "<button onclick='window.sent = true'>Submit</button></form>"
    )

    assert click_account_control(page, "submit") is True
    assert page.evaluate("window.sent") is True


def test_fill_code(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(fixture("workday_code.html"))

    assert fill_code(page, "482913") is True
    assert page.input_value("#code") == "482913"


def test_fill_code_across_digit_boxes(page) -> None:  # type: ignore[no-untyped-def]
    boxes = "".join(
        f"<input id=d{index} maxlength=1 inputmode=numeric aria-label='Digit {index + 1}'>"
        for index in range(6)
    )
    page.set_content(f"<h2>Enter the verification code</h2>{boxes}<button>Verify</button>")

    assert fill_code(page, "482913") is True
    assert "".join(page.input_value(f"#d{index}") for index in range(6)) == "482913"


def test_workday_new_password_page(page) -> None:  # type: ignore[no-untyped-def]
    """Workday's reset page reuses verifyPassword; with no email box it is not create."""
    page.set_content(
        "<h2>Reset Password</h2>"
        "<label for=p>New Password</label><input id=p type=password data-automation-id=password>"
        "<label for=v>Verify New Password</label>"
        "<input id=v type=password data-automation-id=verifyPassword>"
        "<button>Reset Password</button>"
    )

    seen = inspect_account(page)

    assert seen is not None and seen.view == "new_password"


def test_reset_password_button_submits_the_new_password(page) -> None:  # type: ignore[no-untyped-def]
    page.set_content(
        "<h2>Reset Password</h2><form onsubmit='return false'>"
        "<label for=p>New Password</label><input id=p type=password>"
        "<label for=v>Verify New Password</label><input id=v type=password>"
        "<button onclick='window.reset = true'>Reset Password</button></form>"
    )

    assert click_account_control(page, "submit") is True
    assert page.evaluate("window.reset") is True


def test_evidence_redacts_passwords(
    page, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:
    from personal_os_worker.core import secrets
    from personal_os_worker.jobs.browser import page_evidence

    secret = 'pw-secret&"<x'
    monkeypatch.setitem(secrets._cache, secrets.ATS_PASSWORD, secret)
    page.set_content(fixture("workday_sign_in.html") + "<p id=echo></p>")
    page.fill("#password", secret)
    page.evaluate(
        """secret => {
          document.querySelector('#password').setAttribute('value', secret);
          document.querySelector('#echo').textContent = 'You typed ' + secret;
        }""",
        secret,
    )

    evidence = page_evidence(
        page, inspect_fields(page), console_errors=[f"login failed for {secret}"]
    )

    assert evidence
    for name, data in evidence.items():
        if name.endswith(".png"):
            continue
        assert b"pw-secret" not in data, name
