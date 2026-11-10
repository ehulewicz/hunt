"""The account flow: Google, then sign in, create or reset, and park on anything else."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Error, Route, sync_playwright

from personal_os_worker.core.secrets import SecretUnavailable
from personal_os_worker.jobs.account import AccountFlow, AccountOutcome, AccountResult
from personal_os_worker.jobs.account_page import inspect_account
from personal_os_worker.jobs.mail import parse_message

FIXTURES = Path(__file__).parent / "fixtures"
FLOW = (FIXTURES / "account" / "workday_flow.html").read_text()
ATS = "https://acme.wd5.myworkdayjobs.com"
START = f"{ATS}/en-US/External/job/Software-Intern_R1/apply"
PASSWORD = "pw-secret"
EMAIL = "e@x.com"


def mail(name: str) -> EmailMessage:
    return parse_message((FIXTURES / "mail" / name).read_bytes())


class FakeInbox:
    """Hands back prepared messages in order, and records each wait."""

    def __init__(self, messages: list[EmailMessage | None]) -> None:
        self.messages = list(messages)
        self.waits: list[dict[str, Any]] = []

    def await_message(
        self, *, since: datetime, hints: tuple[str, ...], timeout: float = 180.0
    ) -> EmailMessage | None:
        self.waits.append({"since": since, "hints": hints})
        return self.messages.pop(0) if self.messages else None


class Opened:
    """An inbox factory that remembers whether the flow ever opened the inbox."""

    def __init__(self, inbox: FakeInbox) -> None:
        self.inbox = inbox
        self.count = 0

    def __call__(self) -> FakeInbox:
        self.count += 1
        return self.inbox


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
def context(browser) -> Iterator[Any]:  # type: ignore[no-untyped-def]
    opened = browser.new_context()
    opened.route(f"{ATS}/**", lambda route: route.fulfill(content_type="text/html", body=FLOW))
    yield opened
    opened.close()


def open_wall(context: Any, mode: str) -> Any:
    page = context.new_page()
    page.goto(f"{START}?mode={mode}")
    return page


def flow(inbox: FakeInbox | None = None, **kwargs: Any) -> tuple[AccountFlow, Opened]:
    opened = Opened(inbox or FakeInbox([]))
    return (
        AccountFlow(
            email=EMAIL,
            company="Acme",
            password=kwargs.pop("password", lambda: PASSWORD),
            inbox=opened,  # type: ignore[arg-type]
            settle_ms=kwargs.pop("settle_ms", 3_000),
            mail_timeout=1,
        ),
        opened,
    )


def finished(result: AccountResult) -> AccountResult:
    assert PASSWORD not in result.reason
    assert "\n" not in result.reason
    return result


def form_visible(page: Any) -> bool:
    return page.locator("#first").is_visible() and inspect_account(page) is None


def test_creates_verifies_and_signs_in(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "new")
    account, opened = flow(FakeInbox([mail("workday_code.eml")]))

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert result.created is True
    assert form_visible(page)
    assert opened.inbox.waits[0]["hints"][0] == "acme"


def test_ambiguous_sign_in_error_creates(context) -> None:  # type: ignore[no-untyped-def]
    """Workday says "wrong email or password" when there is no account at all."""
    page = open_wall(context, "ambiguous")
    account, _ = flow(FakeInbox([mail("workday_code.eml")]))

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert result.created is True


def test_creates_and_verifies_by_link(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "link")
    account, _ = flow(FakeInbox([mail("workday_verify.eml")]))

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert result.created is True
    assert form_visible(page)
    assert context.pages == [page]


def test_existing_account_signs_in(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "existing")
    account, opened = flow()

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert result.created is False
    assert opened.count == 0
    assert form_visible(page)


def test_wrong_password_resets(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "wrong")
    account, opened = flow(FakeInbox([mail("workday_reset.eml")]))

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert result.created is False
    assert form_visible(page)
    assert page.evaluate("JSON.parse(localStorage.account).password") == PASSWORD
    assert len(opened.inbox.waits) == 1


def google_route(body: str):  # type: ignore[no-untyped-def]
    def handle(route: Route) -> None:
        route.fulfill(content_type="text/html", body=body)

    return handle


CHOOSER = f"""
<h1>Choose an account</h1>
<div role="link" data-identifier="{EMAIL}" tabindex="0" onclick="
  const back = new URLSearchParams(location.search).get('redirect_uri');
  location.href = back;"><div>Ethan</div><div>{EMAIL}</div></div>
"""
CHALLENGE = """
<h1>Welcome</h1><p>Verify it's you</p>
<label for=p>Enter your password</label><input id=p type=password name=Passwd>
<button>Next</button>
"""


def test_google_signs_in(context) -> None:  # type: ignore[no-untyped-def]
    context.route("https://accounts.google.com/**", google_route(CHOOSER))
    page = open_wall(context, "google")
    asked: list[bool] = []

    def password() -> str:
        asked.append(True)
        return PASSWORD

    account, _ = flow(password=password)

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert asked == []
    assert form_visible(page)


POPUP_WALL = """
<h1>Sign in</h1>
<div id=wall>
  <button onclick="window.open('https://accounts.google.com/o')">Sign in using Google</button>
</div>
<div id=form hidden><label for=first>First name</label><input id=first name=first></div>
<script>window.addEventListener('message', () => setTimeout(() => {
  document.getElementById('wall').remove();
  document.querySelector('h1').remove();
  document.getElementById('form').hidden = false;
}, 700));</script>
"""
POPUP_CHOOSER = f"""
<h1>Choose an account</h1>
<div role="link" data-identifier="{EMAIL}" tabindex="0" onclick="
  window.opener.postMessage('signed-in', '*');
  setTimeout(() => window.close(), 400);
"><div>Ethan</div><div>{EMAIL}</div></div>
"""


def test_google_popup_closing_itself_is_a_sign_in(context) -> None:  # type: ignore[no-untyped-def]
    context.route("https://accounts.google.com/**", google_route(POPUP_CHOOSER))
    context.route(f"{ATS}/**", google_route(POPUP_WALL))
    page = open_wall(context, "popup")
    asked: list[bool] = []

    def password() -> str:
        asked.append(True)
        return PASSWORD

    account, _ = flow(password=password)

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert asked == []


def test_google_falls_through_to_password(context) -> None:  # type: ignore[no-untyped-def]
    context.route("https://accounts.google.com/**", google_route(CHALLENGE))
    page = open_wall(context, "google")
    account, _ = flow()

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert page.url.startswith(ATS)
    assert form_visible(page)


def test_rejected_password_parks(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "rule")
    account, _ = flow()

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.PARKED
    assert "Password must contain at least one special character." in result.reason


def test_sign_in_that_keeps_failing_parks(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "stubborn")
    account, opened = flow(FakeInbox([mail("workday_reset.eml")]))

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.PARKED
    assert len(opened.inbox.waits) == 1


def test_mail_timeout_parks(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "new")
    account, _ = flow(FakeInbox([None]))

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.PARKED
    assert "no verification mail" in result.reason


def test_captcha_parks(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "captcha")
    account, _ = flow()

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.PARKED
    assert "captcha" in result.reason
    assert result.captcha is True
    assert page.evaluate("window.clicks") == []
    assert page.input_value("#email") == ""


def test_missing_secret_unavailable(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "existing")

    def missing() -> str:
        raise SecretUnavailable("gcloud is not installed")

    account, _ = flow(password=missing)

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.UNAVAILABLE
    assert result.reason == "gcloud is not installed"
    assert page.input_value("#email") == ""


def test_reason_never_contains_the_password_the_site_echoes(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "rule")
    page.evaluate(
        """() => {
          const original = document.querySelector('[data-automation-id="createAccountLink"]');
          original.addEventListener('click', () => setTimeout(() => {
            document.querySelector('[data-automation-id="click_filter"]').addEventListener(
              'click', () => {
                document.querySelector('#errors').innerHTML = '<div role="alert">Password '
                  + document.querySelector('#password').value + ' must contain a symbol.</div>';
              });
          }, 0));
        }"""
    )
    account, _ = flow()

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.PARKED
    assert "must contain a symbol" in result.reason


def test_account_flow_needs_the_contact_email() -> None:
    from personal_os_worker.jobs.apply_run import account_flow
    from personal_os_worker.jobs.resolve import Knowledge

    assert account_flow(Knowledge(facts={}), "Acme") is None
    built = account_flow(Knowledge(facts={"contact.email": "e@x.com"}), "Acme")
    assert built is not None and built.email == "e@x.com" and built.company == "Acme"


def test_reset_link_off_the_ats_is_never_followed(context) -> None:  # type: ignore[no-untyped-def]
    hits: list[str] = []

    def record(route: Route) -> None:
        hits.append(route.request.url)
        route.abort()

    context.route("https://evil.example/**", record)
    raw = (FIXTURES / "mail" / "workday_reset.eml").read_bytes()
    forged = parse_message(raw.replace(ATS.encode(), b"https://evil.example"))
    page = open_wall(context, "wrong")
    account, _ = flow(FakeInbox([forged]))

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.PARKED
    assert "evil.example" in result.reason
    assert hits == []
    assert page.url.startswith(ATS)


def test_never_types_the_password_on_another_site(context) -> None:  # type: ignore[no-untyped-def]
    create = (FIXTURES / "account" / "workday_create.html").read_text()
    context.route(
        "https://other.example/**",
        lambda route: route.fulfill(content_type="text/html", body=create),
    )
    page = open_wall(context, "offsite")
    account, _ = flow()

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.PARKED
    assert page.url.startswith("https://other.example/")
    assert "other.example" in result.reason
    assert page.input_value("#password") == ""
    assert page.input_value("#email") == ""


@pytest.mark.parametrize(
    "forged",
    [
        # Python reads the host after "@"; the browser reads "\" as a slash.
        "https://evil.example\\@acme.wd5.myworkdayjobs.com",
        # Same last two labels, another host: a shared suffix is not the wall.
        "https://evil.myworkdayjobs.com",
    ],
)
def test_reset_link_that_only_looks_like_the_ats_is_never_followed(  # type: ignore[no-untyped-def]
    context, forged: str
) -> None:
    hits: list[str] = []

    def record(route: Route) -> None:
        hits.append(route.request.url)
        route.abort()

    context.route("https://evil.example/**", record)
    context.route("https://evil.myworkdayjobs.com/**", record)
    raw = (FIXTURES / "mail" / "workday_reset.eml").read_bytes()
    message = parse_message(raw.replace(ATS.encode(), forged.encode()))
    page = open_wall(context, "wrong")
    account, _ = flow(FakeInbox([message]))

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.PARKED
    assert "not followed" in result.reason
    assert hits == []
    assert page.url.startswith(ATS)


def test_create_that_signs_in_after_a_moment_is_not_parked(context) -> None:  # type: ignore[no-untyped-def]
    """Live Workday: a stale error clears at the click, and the form comes a moment later."""
    page = open_wall(context, "slow")
    account, opened = flow()

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert result.created is True
    assert opened.count == 0
    assert form_visible(page)


def test_create_then_verify_first_waits_for_the_link(context) -> None:  # type: ignore[no-untyped-def]
    """Live T-Mobile: after create the page blanks, then sign-in says to verify first."""
    page = open_wall(context, "verifyfirst")
    account, opened = flow(FakeInbox([mail("workday_verify.eml")]))

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert result.created is True
    assert len(opened.inbox.waits) == 1
    assert form_visible(page)
    assert page.evaluate("localStorage.getItem('resent')") is None


def test_unverified_account_resends_then_verifies(context) -> None:  # type: ignore[no-untyped-def]
    page = open_wall(context, "unverified")
    account, opened = flow(FakeInbox([mail("workday_verify.eml")]))

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN, result.reason
    assert result.created is False
    assert page.evaluate("localStorage.getItem('resent')") == "1"
    assert len(opened.inbox.waits) == 1
    assert form_visible(page)


ICIMS = "https://careers-gdms.icims.com"
ICIMS_LOGIN = f"{ICIMS}/jobs/75769/devops-engineer-intern/login"
# What the capture leaves out: Next wakes once there is an email and the box is ticked.
ICIMS_SCRIPT = """<script>
function icimsDisableSaveAndSubmitButtons() {}
const box = document.querySelector('#email');
const agree = document.querySelector('#accept_gdpr');
const next = document.querySelector('#enterEmailSubmitButton');
const wake = () => { next.disabled = !(box.value && agree.checked); };
box.addEventListener('input', wake);
agree.addEventListener('change', wake);
</script>"""
ICIMS_PASSWORD = """<form method="post" action="/done?in_iframe=1">
<h1>Welcome back</h1><input type="email" id="email" name="email" value="">
<input type="password" id="password" name="password"><button type="submit">Sign In</button>
</form>"""


@pytest.fixture
def icims(browser) -> Iterator[tuple[Any, list[dict[str, str]]]]:  # type: ignore[no-untyped-def]
    """An iCIMS tenant: its email step in a frame, then its password page, then the form."""
    step = (FIXTURES / "account" / "icims_email_step.html").read_text() + ICIMS_SCRIPT
    posts: list[dict[str, str]] = []

    def serve(route: Route) -> None:
        request = route.request
        if request.method == "POST":
            posts.append({"url": request.url, "body": str(request.post_data or "")})
        if "/done" in request.url:
            body = '<input id="first" aria-label="First name"><p>Your profile</p>'
        elif "step=email" in request.url:
            body = ICIMS_PASSWORD
        elif "in_iframe=1" in request.url:
            body = step
        else:
            body = f'<a href="/land">LAND</a><iframe src="{ICIMS_LOGIN}?in_iframe=1"></iframe>'
        route.fulfill(content_type="text/html", body=body)

    opened = browser.new_context()
    opened.route(f"{ICIMS}/**", serve)
    yield opened, posts
    opened.close()


def test_email_step_then_password_signs_in(icims) -> None:  # type: ignore[no-untyped-def]
    context, posts = icims
    page = context.new_page()
    page.goto(ICIMS_LOGIN)
    page.wait_for_selector("iframe")
    page.frames[-1].wait_for_selector("#enterEmailSubmitButton", state="attached")
    account, _ = flow()

    result = finished(account.run(page))

    assert result.outcome == AccountOutcome.SIGNED_IN
    assert "step=email" in posts[0]["url"]
    assert EMAIL in posts[0]["body"] and 'name="accept_gdpr"' in posts[0]["body"]
    assert PASSWORD in posts[1]["body"]
    assert result.captcha is False
    assert page.frames[-1].locator("#first").is_visible()
