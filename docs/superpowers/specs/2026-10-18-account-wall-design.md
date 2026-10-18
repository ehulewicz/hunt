# Spec: Getting `os hunt apply` past the account wall

## Context and why

Account walls stop every run. Workday, iCIMS, Taleo, SuccessFactors and similar
sites want you signed in, often with a freshly created and email-verified account,
before they show the form. Today the worker can't get past that:

- It never touches account controls. `jobs/gateway.py:33-39` excludes sign in,
  sign up, register, create account and forgot.
- It drops password inputs at capture (`jobs/browser.py:1272`).
- It reads no email.

So the run reports `navigation_wall` (`autofill_session.py:503-512`) and waits
for you to do it by hand.

This spec gets past the wall automatically, running locally exactly as today:
WSL, the local FastAPI, and native Windows Chrome on the
`C:\Users\eahtr\personal-os-chrome` profile, driven over CDP.

## Goals

On an account wall, `os hunt apply` gets to the form without you:

1. **Google first.** Use "Sign in / Continue with Google" with the professional
   account `alex.example@example.com`, already signed into the automation profile,
   whenever the site offers it.
2. **Sign in.** Otherwise, sign in with the `contact.email` fact and one reusable
   password.
3. **Create.** If no account exists, create one, read the verification email,
   complete verification, then continue.
4. **Reset.** If an account exists with a different password, reset it to the
   reusable password through the emailed reset link.

## Non-goals (this spec)

- **The VM and native Linux Chrome.** Deferred: for now it runs locally under
  Claude remote control.
- **Autonomous Submit, cover-letter automation, and the long-answer bank,
  interests and projects work.** They come with the later full-automation spec.
  The user still owns Next and Submit.
- **Changing the Personal OS login allowlist.** `backend/.../auth.py:106` stays
  single-user. It gates the app, not applications.
- **`os hunt apply <url>`.** It's being finished in separate work. This spec plugs
  into whatever apply entry point exists.

## Anti-goals (a failure even if it "works")

- **A password where it can be stored or uploaded.** The ATS password or the Gmail
  app password must never appear in the database, the archive, facts, run events,
  failure evidence (HTML, screenshots or field snapshots), logs or terminal output.
- **A model call in the account flow.** It's deterministic, so the `claude -p`
  call-site list in AGENTS.md is unchanged.
- **Clicking the application's own Next or Submit.** The account-control exception
  holds only while an account page is detected.
- **Solving or bypassing captchas.** A captcha on an account page parks the run for
  you, as captchas do elsewhere (`browser.py:453-478`).
- **New database entities or columns.** There's no accounts table, and the flow is
  stateless.
- **Breaking ATSes that work today**, such as Greenhouse, Lever and Ashby, which
  show "Apply with Google/LinkedIn" buttons next to a real form.

## Decided by the user

- **The account email is `alex.example@example.com`.** It's used for Google sign-in
  on ATS sites and as the email/password account email. The `contact.email` fact is
  set to it. It's an existing fact, so there's no schema change.
- **One reusable ATS password.**
- **Secrets live in GCP Secret Manager**, project `example-gcp-project`, and are read
  at run time. `gcloud` is already installed and signed in on this machine.
- **Local only for now.**

## Design

### 1. Secrets: `core/secrets.py`

- **Read:** `gcloud secrets versions access latest --secret=<name>
  --project=example-gcp-project`, run as a subprocess. That's the worker's
  shell-out style, with no new Python dependency.
- **Lifetime:** values are held in memory for the process lifetime and never
  written to disk.
- **Secrets:**
  - `jobs-ats-password`: the reusable ATS password.
  - `jobs-gmail-app-password`: the Gmail app password for IMAP on the account
    email.
- **Missing gcloud or a missing secret:** the account flow is unavailable. The run
  falls back to today's behavior (report the wall and watch the page) with a
  one-line reason, and doesn't crash.
- **`os doctor`** checks that both secrets are readable. It prints no values.

### 2. Mail: `jobs/mail.py`

The inbox is read-only over IMAP.

- **Connection:** `imaplib` to `imap.gmail.com:993`. The user is `contact.email`,
  and the password is the app password.
- **API:** `await_message(since, sender_hint, timeout)`.
  - It polls INBOX every few seconds for messages received after `since`, the
    moment the worker clicked create or reset.
  - It prefers senders matching the ATS host (`myworkday`/`workday`, `icims`,
    `taleo`, `successfactors`) or the company name.
  - It returns the newest match.
- **Extraction (pure functions):**
  - A verification or reset link: the first `https` link whose text or URL has
    verify, confirm, activate, validate or reset, on the ATS's domain family.
  - Or a 4–8 digit code near "code".
- **Read-only:** it fetches with `BODY.PEEK`. It never deletes, sends, labels or
  marks mail read.
- **Timeout:** 3 minutes by default. Then it parks.

### 3. Detecting the account page

Add `ApplicationPageState.ACCOUNT` to `jobs/page_state.py`.

- **Triggers:**
  - A visible password input, or
  - an email input together with a sign-in, create-account or register control,
    or
  - a visible "Sign in with / Continue with Google" control.
- **Precedence:** `ACCOUNT` applies **only when the page is not already an
  application form**, meaning `looks_like_application_form(fields)` is false and
  there's no submit control. A real form with an "Apply with Google" or "save your
  progress with a password" extra stays `FORM` and is filled as today.
- **Password inputs:** capture must see them for detection, and to fill them in the
  account flow only. They stay out of `inspect_fields` output, the planner and the
  archive. The skip filter at `browser.py:1272` is unchanged.

### 4. The account flow: `jobs/account.py`

`AutofillSession.wait_for_form` handles `ACCOUNT` before `pass_the_gate`. It calls
the account flow, which returns `signed_in`, `parked` or `unavailable`. After
`signed_in` the loop just continues. The account step is the gate in front of the
gate.

A fallback chain runs in order, and parking happens only when nothing works:

1. **Google** (if the control exists). Click it. On `accounts.google.com`, in the
   same tab or a popup:
   - Pick the `contact.email` tile.
   - Accept continue or consent.
   - Wait to return to the ATS.

   If Google itself challenges (asks for a password, 2FA or "verify it's you"),
   or the ATS refuses the Google identity, for example "an account with this email
   already exists", go back to the account page and **fall through to step 2**.
2. **Sign in.** Fill email and password and click sign-in. Leaving the `ACCOUNT`
   state means `signed_in`.
3. **Create** (sign-in says there's no account, or the site leads with sign-up):
   1. Open create account.
   2. Fill email, password and confirm password.
   3. Tick only the account-terms consent. Reuse `is_consent_field`.
   4. Click create.
   5. Verify: open the mailed link in a new tab and close it when done, or fill
      the code.
   6. Sign in again if asked.
4. **Reset** (sign-in says the password is wrong):
   1. Forgot password, then the email.
   2. The mailed reset link.
   3. Set the reusable password.
   4. Sign in.
5. **Park:** a captcha, a mail timeout, a missing secret, or nothing recognized.
   - Print one line saying what it saw.
   - `report_page_failure("account_wall", ...)`, redacted, so the existing
     `os hunt apply failures` tooling captures it.
   - Then today's "I am watching the page" standby.

**Recognition.** Sign-in, create, forgot and Google controls, plus the no-account
and wrong-password error phrases, are found with wording sets. They live beside
the existing wording in `jobs/sites.py`/`options.py`, with Workday's
`data-automation-id` selectors where wording isn't enough. **Workday is the first
target.** Others are best-effort through generic wording, grown from captured
failures.

**Rule change, documented.** While a page is `ACCOUNT`, the worker may click
account controls and the account form's own submit button. Application Next and
Submit are still never clicked. The gate exclusion list in `gateway.py:33-39`
stays for gates.

**Redaction.**
- `page_evidence`, `record_snapshot` and `capture_final_snapshot` strip every
  password input's value.
- Any captured text has both secret values replaced with a placeholder.
- Tests assert neither secret appears in any evidence payload.

### 5. Docs (same change)

- **`workers/README.md`:**
  - The account flow and the secrets.
  - The one-time setup:
    1. Sign into `alex.example@example.com` in the automation Chrome profile.
    2. Set `contact.email` to it.
    3. Turn on 2-Step Verification on it and create a Gmail app password.
    4. Store both secrets in Secret Manager.
- **`docs/jobs-cli.md` "Headed apply":** the account-wall exception to "your
  account's sign-in are never candidates".

## Left to the builder

- Module boundaries within `jobs/`, the wording sets and the Workday selectors.
- The IMAP poll interval, sender heuristics and link scoring.
- Generating the reusable password before storing it. Suggested: 16 chars with
  upper, lower, digit and symbol.

## Calls I made (check these)

- **IMAP plus an app password instead of the Gmail API.** Testing-mode OAuth
  refresh tokens expire every 7 days, and `gmail.readonly` is a restricted scope.
  This needs 2FA on the professional account.
- **`gcloud` CLI subprocess** rather than the Secret Manager client library.
- **Password reset is in scope.** It folds old manual accounts onto one password.
- **Stateless:** try Google, then sign-in, then create or reset. No schema change.
- **A fallback chain:** Google failures fall through to email and password before
  parking.
- **`ACCOUNT` never overrides a fillable form.**
- **Workday first.**

## Verification

- **Gates:**
  - `cd workers && uv run ruff check . && uv run mypy src/ && uv run pytest -q`
  - The same command in `backend/`, which should be unchanged.
- **Unit tests:**
  - Mail parsing on `.eml` fixtures: a Workday verify link, a code email and a
    reset email.
  - Classification on saved HTML fixtures:
    - Workday sign-in, create and verify pages are `ACCOUNT`.
    - Greenhouse, Lever and Ashby forms with "Apply with Google" are `FORM`.
  - The fallback chain's transitions, against fake page outcomes.
  - Redaction: neither secret appears in evidence or snapshots.
  - Missing-secret fallback to standby.
- **Manual, on the laptop:**
  - A Workday posting with no account: it's created, verified and signed in, and
    the form fills.
  - A rerun on the same tenant: it signs in directly.
  - A Greenhouse posting: no account step.
  - `os doctor` reports the secrets readable.
