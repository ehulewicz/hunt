# Spec: Autonomous submission through a device's real Chrome

## Context and why

On 2026-11-16 Grok (on the box) applied to Pivotal's Lever posting through
EXAMPLEPC's Chrome, using the `WS /cdp` passthrough added in `10e84a0`. The fill
was complete, but the press came back as **"There was an error verifying your
application. Please try again."** Lever had cleared the form.

Ethan then submitted the same form himself, and it went in; the Lever
confirmation email arrived at 3:59 PM ET. **The click that passed was a
person's.** Whether it was a live-view click (CDP `Input`) or a physical click
at the PC isn't recorded, so it proves only that this profile and IP can pass.

The IP, profile and history were the same both times. What differed was how the
browser was driven:

- **Playwright `connect_over_cdp` enables `Runtime` in every frame**, the
  hCaptcha frames included.
- **The input was scripted.** Values were set with no pointer path, the resume
  went in as a script-built `DataTransfer` (an untrusted `change` event), and
  `scrollIntoView` ran just before the press.
- **Chrome was running with `--remote-debugging-port`** and had a debugger
  attached during the press.

Any of these can lower Lever's invisible hCaptcha score. Nothing yet shows
which one does.

There was also an incident. A fill script that was still running reopened
`/apply` *after* Ethan's submission and began refilling. Nothing went in twice,
but nothing structural stopped it either.

**Goal: Ethan never presses Submit.** This spec is an iteration plan to find a
route that submits on its own through EXAMPLEPC's real Chrome. It starts with
cheap experiments and moves up to heavier ones, with a safe channel, binding
locks and an honest scorecard around them.

## Goals

1. **Fully autonomous submission** on Lever (invisible hCaptcha), then
   Greenhouse and Ashby, through EXAMPLEPC's real Chrome and residential IP.
2. **Experiments, not hope.** Each route is one experiment with a fixed change,
   a measurement and a budget. The plan moves on when a budget is spent.
3. **A failed attempt is a failure, never a handoff.** It's recorded with
   diagnostics, and the next attempt uses the next route. At most Ethan gets a
   failure notification.
4. **The device enforces a per-listing submitted lock and an instant stop.**
   Nothing a driver does can reopen, refill or resubmit a listing that may have
   gone in.

## Non-goals (this spec)

- **Changing `os hunt apply`'s own Playwright path.** The winning route gets
  ported there afterwards.
- **Paid captcha-solving services or third-party solver APIs.**
- **New database entities or columns.**

## Anti-goals (a failure even if it "works")

- **A second press** on a listing that is locked or may have gone in.
- **Asking Ethan to press Submit**, or parking a run for his click.
- **Any channel that reaches other tabs, cookies, storage or `Network`**, or
  navigates off the listing's ATS host.
- **A driver that keeps acting after Stop**, after Ethan touches the live view,
  or after its lease ends.

## Decided by the user

- **Ethan never presses Submit.** Iterate until a route passes.
- **A rejection is marked failed.** The next iteration tries a different
  approach.
- **The test bed is real, eligible listings** (below), one attempt each.

## Design

### 1. The test bed: real listings, one attempt each

Each attempt uses up its listing: Lever clears the form on rejection, and a
retry risks a duplicate.

- **Candidates.** Real eligible listings from `os hunt list --ats lever`, in
  board order. Excluded:
  - FAANG and Palantir;
  - postings that require a PhD, or a master's/PhD being pursued now;
  - closed postings (the Lever posting API returns 404); and
  - listings with any application row.
- **One attempt per listing, ever.**
  - A rejected listing is marked `failed_captcha` in the run log and its
    application notes, and isn't tried again by this plan.
  - A listing whose outcome is unconfirmed is never pressed again.
  - Ethan may later decide to apply to a burned listing by hand. That's his
    call, outside this plan.
- **Confirmation is triangulated.** The ATS confirmation URL or wording
  (Lever: `/thanks`) is checked, and so is the professional Gmail for the ATS's
  "Thank you for your application" mail within 10 minutes. Only then is it
  `submitted` and marked applied (`mark-applied` / `--done` path).
- **Scorecard.** Each attempt is one line in `state_dir()/drive/attempts.jsonl`:
  - listing, ATS, experiment number and its knobs;
  - whether a debugger was attached at the press;
  - input kind (CDP or OS);
  - fill duration;
  - outcome: `submitted`, `captcha_rejected`, `challenge_shown`,
    `validation`, or `unconfirmed`;
  - whether the confirmation email arrived; and
  - a screenshot path.

### 2. The iteration plan

Run in order. An experiment **passes** at 2 of 2 Lever submissions accepted
(confirmation plus email). It **fails** at 2 rejections, or 3 attempts without
2 passes, and the plan moves to the next experiment. Each experiment keeps every
improvement before it.

| # | Experiment | What changes | Cost |
|---|---|---|---|
| 1 | **Device click, driver gone** | Fill through `/drive` (§3) with human-shaped CDP input. At the press the driver detaches and the agent waits 3–8 s, then makes one human-shaped `Input.dispatchMouseEvent` click through the live-view session. No `Runtime`, no frame attach. | cheap: mostly built |
| 2 | **OS click, no CDP at press** | As 1, but before the press the agent detaches **every** CDP session (including the live view's screencast), then clicks with OS-level input on Windows via `SendInput`, so the events are trusted from the OS. Screen coordinates come from the last `DOM.getBoxModel`, window bounds and DPI; the window is foreground and unminimized. | cheap–medium |
| 3 | **Human timing and dwell** | Keeps 1 or 2, whichever did better. Fill time stretched to 4–8 min, real reading pauses, the pointer wandering over the description first, focus and blur on every field, the resume chosen through the page's own chooser (§3). | cheap |
| 4 | **Whole fill by OS input** | No CDP input at all. Fields are found from one DOM/AX read (or screenshots) and filled by OS mouse and keyboard; the resume goes through the native Windows file dialog, typed path plus Enter. CDP is used for reading only, and nothing is attached during typing or the press. | medium |
| 5 | **No debug port at the press** | Chrome itself stops advertising automation. Variant A: after the fill, close the debug port by restarting Chrome without `--remote-debugging-port` with session restore, so the filled form survives (verify), then press by OS input. Variant B: a second, normal Chrome profile with no debug port that the agent drives only by OS input and screenshots. Warm it first with ordinary browsing and a Google sign-in. | heavy |
| 6 | **Visible challenge solving** | If any experiment gets a visible hCaptcha challenge instead of a silent rejection, the agent screenshots the challenge frame. A vision model (the driver) picks the tiles, and the agent clicks them by the same input route. Lives in its own module, kept out of `os hunt apply`. Overrides the account-wall spec's "never solve captchas" for this channel only. | medium; only when a challenge appears |

**Cheap first.** 1 and 3 need no new Windows tooling. 2 needs one small
`SendInput` helper in the agent (Python `ctypes`, no AutoHotkey or pyautogui
dependency). 4 and 5 are larger. 6 runs only if challenges appear.

**Measurement.**
- **Accepted:** Lever's `/thanks` plus the confirmation email.
- **Rejected:** the "There was an error verifying your application" banner
  with the form cleared.
- **Challenge:** a visible hCaptcha frame (`frame=challenge`, larger than
  100×100 and visible).

The scorecard line is written before the next attempt starts.

**Failure notification only.** Each failed attempt sends Ethan one line ("Lever
rejected attempt #n on <company> — experiment k; next: k+1"), through the
heartbeat's `problem` text and Grok's chat. It never asks him to act.

**Stopping the plan.**
- **Success:** if an experiment passes, it becomes the default route, and the
  plan widens to 2 Greenhouse and 2 Ashby postings with the same route.
- **No route found:** if 1–5 all fail, the spec is revisited with the scorecard
  before any more listings are spent.
- **Budget:** at most 15 listings for the whole plan.

### 3. `WS /drive` replaces `WS /cdp` (`device/drive.py`)

`/cdp` is removed. `/drive` keeps its admission rules: a bearer token in the
header only, any `Origin` refused, and the same `TokenVerifier`. It adds the
following.

- **Lease.** The first message is `{"type": "lease", "listing_id", "url"}`.
  - The agent refuses the lease if:
    - the listing is locked (§5);
    - a page run or another lease is active;
    - the drive switch is off (§6); or
    - the URL's host isn't an ATS host.
  - It attaches to **one** tab only, with `Target.attachToTarget {flatten: true}`
    and never `setAutoAttach`, so out-of-process frames like hCaptcha are never
    attached.
  - It expires after 10 idle minutes or 45 minutes in total.
- **Allowlist** (forwarded to that page session only; anything else is refused
  and logged):

  | Domain | Methods |
  |---|---|
  | Page | `enable`, `navigate` (lease host, lock checked), `reload` (lock checked), `getNavigationHistory`, `captureScreenshot`, `getLayoutMetrics`, `setInterceptFileChooserDialog`, `createIsolatedWorld` |
  | Input | `dispatchMouseEvent` (including `mouseWheel`), `dispatchKeyEvent`, `insertText` |
  | DOM | `getDocument`, `querySelector(All)`, `getBoxModel`, `getContentQuads`, `describeNode`, `getAttributes`, `getOuterHTML`, `setFileInputFiles` (chooser node only) |
  | Accessibility | `getFullAXTree`, `queryAXTree` |
  | Runtime | `evaluate` only in the lease's isolated world, read-only |

  `Runtime.enable`, `Debugger`, `Network`, `Storage`, `Target`, `Browser`,
  `Emulation` and `Fetch` are never forwarded.
- **Reading without `Runtime`.**
  - DOM and Accessibility come first: AX names give labels, roles, values and
    required states, and `getBoxModel` gives click points.
  - Isolated-world `evaluate` is allowed for reads only and logged per use, so
    the scorecard can show whether it correlates with rejections.
- **The press is its own message.** `/drive` never forwards a click on a
  control matching `SUBMIT_CONTROL`. The driver sends
  `{"type": "submit", "experiment": k}`. The agent then:
  1. sets the lock to `pending`;
  2. closes the driver's session; and
  3. presses by experiment *k*'s route.
- **Human-shaped CDP input** (`device/drive_client.py`, shared by every
  driver):
  - **Pointer:** curved paths of 8–20 `mouseMoved` steps with jitter, a jittered
    target inside the box, and a dwell before press.
  - **Keys:** `keyDown`/`char`/`keyUp` per character, 40–140 ms apart, with
    pauses.
  - **Selects:** by keyboard.
  - **Scrolling:** `mouseWheel` steps only.
  - **Resume:** a real click on the page's Attach control with the chooser
    intercepted, then `DOM.setFileInputFiles` on the chooser's node with a file
    the agent wrote itself. That's the tradeoff for experiments 1–3; 4 and 5
    use the native dialog.

### 4. Outcomes and captcha rejection

- **Wording.**
  - Captcha-rejection phrases live beside `SUBMIT_CONTROL` in `jobs/submit.py`
    as `CAPTCHA_REJECTED`, seeded with Lever's "There was an error verifying
    your application".
  - Others are added from captured pages (`report_page_failure("captcha_rejected", …)`).
  - `os hunt apply --submit` uses the same set.
- **Outcomes after a press:**

  | the page | the outcome |
  |---|---|
  | confirmation (plus email within 10 min) | **submitted**: lock; mark applied |
  | captcha rejection | **failed** (`captcha_rejected`): lock as burned; scorecard; failure notice; never pressed again |
  | visible challenge | experiment 6 if enabled, else **failed** (`challenge_shown`) |
  | validation errors | **held**: lock released for one fix-and-press by the same route |
  | anything else or no change | **unconfirmed**: locked; never pressed again; Gmail decides later |

  Lever clears the form on rejection. Nothing refills a rejected listing.

### 5. Locks, stop and concurrency (agent side)

- **Per-listing lock** in `state_dir()/drive/locks.json`, keyed by listing id
  and normalized posting URL.
  - **States:** `pending`, `submitted`, `unconfirmed`, `burned`.
  - **When it's set:** at the `submit` message; when a confirmation URL or
    wording appears in **any** tab (`Target.targetInfoChanged`); on the Hunt
    page's Submitted; and on rejection (`burned`).
  - **Checks:** the agent refuses a lease, `Page.navigate`, `Page.reload` and
    any `Input` on a tab whose URL matches a locked listing. It also refuses a
    lease whose listing has an application at `applied` or later in the API.
  - **Release:** only `held` (validation) releases it. Today's incident is
    refused at the first `navigate`.
- **Stop is enforced by the device.** These end the lease and close the
  websocket within one message:
  - Stop on the Hunt page;
  - `os device drive off`;
  - any live-view input from Ethan;
  - lease expiry; and
  - the tab closing or leaving the lease host.

  The client also checks its own stop flag before each action, but correctness
  doesn't depend on it.
- **One driver per device; never during a page run.**

### 6. Security of the channel

| `/cdp` today (`10e84a0`) | `/drive` |
|---|---|
| whole browser: all tabs, cookies, Google session | one tab per lease, allowlisted methods, ATS hosts only |
| always on | `os device drive on\|off` plus a Hunt-panel toggle, **default off**; off closes any lease at once |
| no record | `state_dir()/drive/audit.jsonl`: time, lease, method, a param summary (lengths, never typed values), refusals; rotated at 5 MB |
| token only | token only, no Origin, plus the lease's listing checked against the API |

OS-level input (experiments 2, 4 and 5) is triggered only by a `submit` or
fill message on an active lease. It's confined to the lease's Chrome window
bounds, and nothing outside that window is ever clicked or typed into.

### 7. Docs (same change)

- **`workers/README.md`:**
  - `drive.py` and `drive_client.py`.
  - The allowlist, locks, switch, audit log and OS-input helper.
  - Remove `/cdp`.
- **`docs/jobs-cli.md`:** `os device drive on|off`, the captcha-rejected
  outcome, and the attempts scorecard.

## Left to the builder

- The timing distributions within the stated ranges.
- The AX-to-field mapping on top of `jobs/` field kinds.
- DPI and window-coordinate math for `SendInput`.
- Whether Chrome session restore keeps a filled Lever form (it decides whether
  5A is viable before any listing is spent on it).

## Calls I made (check these)

- **The pass bar is 2 accepted Lever submissions per experiment,** with a
  15-listing ceiling for the plan.
- **Remove `/cdp`** rather than keep it behind the switch.
- **`SendInput` through `ctypes`** rather than AutoHotkey or pyautogui: no new
  dependency.
- **Experiment 6 overrides "never solve captchas"** for this channel only.
- **No schema change.** Failure notices ride in the heartbeat's `problem` text;
  locks are device-local plus API status.

## Open question for Ethan

1. **Long essays.** On listings with long essays, may the driver write grounded
   drafts the way `os hunt apply` does today, or should the test bed skip
   listings with essays until a route passes?

## Verification

- **Gates:** `cd workers && uv run ruff check . && uv run mypy src/ && uv run
  pytest -q`.
- **Unit tests** (fake Chrome, as in `test_device_server.py`):
  - Allowlist refusals.
  - Lock refusals on `navigate`, `reload`, `Input` and lease.
  - Stop paths closing within one message.
  - Outcome classification on fixture pages (Lever `/thanks`, the rejection
    banner, validation errors, no change).
  - The audit log never holding typed values.
  - `SendInput` coordinate mapping against recorded window bounds.
- **Live:** the experiments themselves, each attempt one scorecard line plus
  the Gmail check.

## Rollout

1. Ship `/drive` (switch off), remove `/cdp`, and add the scorecard. The device
   updates on its next run.
2. Ethan turns the switch on for EXAMPLEPC; Grok runs experiment 1 on the next
   two eligible Lever listings.
3. Advance through the table by the pass and fail rules, posting the scorecard
   after each experiment.
4. Port the winning route to `os hunt apply --submit`, then widen to Greenhouse
   and Ashby.
