"""The account wall: recognized as its own page, and never mistaken for a form."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Error, sync_playwright

from personal_os_worker.jobs.account_page import inspect_account
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
