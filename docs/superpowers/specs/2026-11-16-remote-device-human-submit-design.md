# Spec: An outside driver applies through a device's real Chrome, and a person's click submits

## Context and why

On 2026-11-16 Grok (on the box) applied to Pivotal's Lever posting through
EXAMPLEPC's Chrome, using the `WS /cdp` passthrough added in `10e84a0`. The
fill was complete, but the press came back as **"There was an error verifying
your application. Please try again."** Lever had cleared the form. Ethan then
submitted the same form by hand, and it went in; the Lever confirmation email
arrived at 3:59 PM ET.

The IP, profile and history were the same both times. What differed was how the
browser was driven:

- **Playwright `connect_over_cdp` enables `Runtime` in every frame**, the
  hCaptcha frames included. Lever's invisible hCaptcha, which scores its token
  server-side, can see a debugger.
- **The input was scripted.** Values were set with no pointer path, the resume
  went in as a script-built `DataTransfer` (an untrusted `change` event), and
  `scrollIntoView` ran just before the press.
- **There was an incident.** A fill script that was still running reopened
  `/apply` *after* Ethan's submission and began refilling. Nothing went in twice,
  but nothing structural stopped it either.

`/cdp` also hands any token holder on the tailnet the whole browser: every tab,
every cookie, the Google session. That was fine for one supervised attempt, but
too wide to keep.

This spec makes an outside driver look like a person at the page level, gives
the press to a person when it has to, and makes "already submitted" and "stop"
binding on the device rather than on the driver's good behavior.

## Goals

1. **A narrow drive channel.** An outside driver (Grok on the box today) fills
   one tab of a device's Chrome. The channel is page-level and allowlisted,
   with no `Runtime.enable`, no frame-wide attach and no Playwright.
2. **Human-shaped input.** Every value goes in through `Input` events, with
   pointer paths, paced keys and wheel scrolling. The resume goes in through
   the page's own file chooser.
3. **The press.** The driver lets go first. Then one human-shaped click comes
   from the agent; if that's refused, Ethan clicks Submit himself from the Hunt
   page.
4. **Captcha rejection is an outcome.** It parks, never retries, and says the
   form was cleared.
5. **The device enforces** a per-listing submitted lock and an instant stop.
   Nothing the driver does can reopen or refill a submitted listing.

## Non-goals (this spec)

- **Changing `os hunt apply`'s own Playwright path.** The same finding probably
  applies to it, but it gets its own spec once this channel proves out.
- **Solving captchas.** An image puzzle or a checkbox is Ethan's, as everywhere
  else.
- **A model inside the agent.** The agent only mediates. The driver decides
  what to type.
- **New database entities or columns.** The lock uses the existing application
  status plus device-local state.

## Anti-goals (a failure even if it "works")

- **Any path that reaches another tab, cookies, storage, downloads or `Network`**,
  or navigates off the listing's ATS host.
- **A second press** of any kind on a listing that is locked, unconfirmed or
  rejected-and-not-yet-refilled.
- **A driver that keeps acting after Stop**, after Ethan touches the live view,
  or after its lease ends.
- **Typing secrets through the channel.** Account walls stay with `os hunt
  apply`'s account flow.

## Design

### 1. `WS /drive` replaces `WS /cdp` (`device/drive.py`)

`/cdp` is removed. `/drive` keeps its admission rules: a bearer token in the
header only, any `Origin` refused, and the same `TokenVerifier`. It adds the
following.

- **Lease.** The first message is `{"type": "lease", "listing_id", "url"}`.
  - The agent refuses the lease if:
    - the listing is locked (§5);
    - a page run or another lease is active (one driver at a time; a driver
      never shares a tab with `os hunt apply`);
    - the device's drive switch is off (§4); or
    - the URL's host isn't an ATS host (`jobs.lever.co`, `boards.greenhouse.io`,
      `job-boards.greenhouse.io`, `jobs.ashbyhq.com`, …, reusing the ATS
      detection in `jobs/`).
  - The lease opens or reuses **one** tab for that URL and attaches to it
    alone: `Target.attachToTarget {flatten: true}`, never `setAutoAttach`. Out-of-process
    frames such as hCaptcha are never attached.
  - It expires after 10 idle minutes or 45 minutes in total.
- **Allowlist.** Every driver message is `{"id", "method", "params"}` and is
  forwarded to that one page session **only** if its method is on this list.
  Anything else is answered with an error and logged.

  | Domain | Methods | Why |
  |---|---|---|
  | Page | `enable`, `navigate` (lease host only, lock checked), `reload` (lock checked), `getNavigationHistory`, `captureScreenshot`, `getLayoutMetrics`, `setInterceptFileChooserDialog`, `createIsolatedWorld` | load, see, and catch the file chooser |
  | Input | `dispatchMouseEvent` (including `mouseWheel`), `dispatchKeyEvent`, `insertText` | every value and every scroll |
  | DOM | `getDocument`, `querySelector`, `querySelectorAll`, `getBoxModel`, `getContentQuads`, `describeNode`, `getAttributes`, `getOuterHTML`, `setFileInputFiles` (chooser node only, §2) | find fields and read them |
  | Accessibility | `getFullAXTree`, `queryAXTree` | labels, roles, values, required |
  | Runtime | `evaluate` **only** with `contextId` from that lease's isolated world, `returnByValue`, read-only expressions | last-resort reads |

  `Runtime.enable`, `Debugger.*`, `Network.*`, `Storage.*`, `Target.*`,
  `Browser.*`, `Emulation.*` and `Fetch.*` are never forwarded. Only these events
  reach the driver: `Page.loadEventFired`, `Page.frameNavigated` (main frame),
  `Page.fileChooserOpened` and `Page.navigatedWithinDocument`.
- **Reading fields without `Runtime`.** DOM and Accessibility are the primary
  readers. The AX tree gives each control's accessible name (its label), role,
  value, required and expanded states. `DOM.getBoxModel` gives the click point.
  None of this runs script in the page, and neither domain shows up to page
  JavaScript the way the `Runtime.enable` console serialization leak does.
  - **Isolated world (the tradeoff).** Some reads are much easier in script,
    such as the text of a custom combobox's open list. For those the driver may
    evaluate in a `Page.createIsolatedWorld` context. That's invisible to page
    scripts and doesn't enable `Runtime`, but it's still a debugger action.
  - **Decision:** reads only, never to set a value or fire an event, and each
    use is logged so we can see if it correlates with rejections.

### 2. Human-shaped input (driver side, `device/drive_client.py`)

A small client library ships in `workers/` so any driver (Grok, a test, later
`os hunt`) gets the same hands.

- **Pointer.** Before every click it moves along a curved path from the last
  point: 8–20 `mouseMoved` steps over 150–450 ms with ±2 px jitter. The target
  point is jittered inside the inner 60% of the box. Press and release are
  50–120 ms apart, and there's a hover dwell of 80–250 ms.
- **Keys.** Each character is `keyDown`/`char`/`keyUp` with the right `key`,
  `code` and `windowsVirtualKeyCode`, 40–140 ms apart. It pauses 300–900 ms
  every 8–20 characters and after punctuation. `insertText` is only for
  characters with no key; long answers type at the same rate (a 600-character
  essay takes about 1 minute). The client never sets `.value`.
- **Selects.** It clicks to focus, then types the option's leading text or
  uses arrows plus Enter. Custom comboboxes: click, type, then click the option
  found by AX name.
- **Scrolling.** Only `mouseWheel` in 80–120 px steps toward the target until
  its box is in view, never `scrollIntoView`.
- **Resume.** It turns on `Page.setInterceptFileChooserDialog`, then clicks
  the page's own Attach button with a real pointer event. The page opens its
  chooser with a genuine user activation, and the agent gets
  `Page.fileChooserOpened {backendNodeId}`. The agent then calls
  `DOM.setFileInputFiles` on that node with a file it wrote itself.
  - **The file.** The agent fetches the listing's resume (the tailored PDF if
    one exists, otherwise the published `/v1/public/resume.pdf`) into
    `state_dir()/drive/` and passes the Windows path. The driver never sends a
    path or bytes.
  - **Tradeoff.** A bare `DOM.setFileInputFiles` with no click is simpler, but
    the page then sees a `change` event with no preceding user gesture. A real
    native dialog driven by OS input would need a desktop-automation dependency
    on Windows.
  - **Pick:** an intercepted chooser opened by a real click. The script-built
    `DataTransfer` used on 11-16 is gone.

### 3. The press

`/drive` never forwards a click on a control whose AX name matches
`SUBMIT_CONTROL` (`jobs/submit.py`). Pressing is its own message.

1. **`{"type": "ready_to_submit"}`.**
   - The agent records the listing as **pending** (§5).
   - It closes the drive session, detaches from the tab (removing the driver's
     `DOM`/`Accessibility` state), and waits 3–8 s.
   - The driver's websocket is closed and can't be reopened for this listing.
2. **Attempt (a): one agent click.**
   - Through the live-view input path (`stream.py`'s page session, `Input`
     only), the agent moves to and clicks the submit button once.
   - Before attaching, it finds the box with `DOM.getBoxModel`, then detaches
     `DOM`.
   - It reads the outcome from `Page.frameNavigated`/`loadEventFired` and the
     page text (one `captureScreenshot` plus an AX read after load):

   | the page | the outcome |
   |---|---|
   | ATS confirmation (`/thanks`, "application received", …) | **submitted**: lock; mark applied with the URL |
   | captcha rejection (§4 wording) | **rejected**: go to (b) |
   | an image puzzle or checkbox | **parked**: (b), the tick is Ethan's |
   | validation errors | **held**: lock released; the driver may lease again to fix them |
   | anything else, or no change | **unconfirmed**: locked, never pressed again |

3. **Fallback (b): a handoff to Ethan.**
   - On rejected or parked, the run becomes `awaiting_submit`.
   - Because Lever clears the form on rejection, the agent grants **one**
     refill lease. A rejection proves nothing went in. The submit control stays
     driver-forbidden.
   - The Hunt page shows the tab with a banner: "Ready for your click: press
     Submit in the form (Lever refused an automated press)." Ethan clicks Submit
     in the live view or at the PC.
   - The run watches for confirmation and locks on it. Ethan's existing
     **Submitted** control also locks.
   - **Notification.** The device heartbeat carries `awaiting_submit` in the
     existing `problem` text slot, with no schema change. The Hunt devices panel
     shows it, and the driver (Grok) tells Ethan in chat with the device link.
     No Windows toast in v1.

**Default:** (a) once, then (b). On a site where (a) has already been rejected
in the last 7 days, the agent goes straight to (b), so Ethan never has to wait
through a refill. That memory is a device-local `drive/rejections.json`, keyed
by ATS host.

### 4. Captcha rejection and the drive switch

- **Wording.**
  - Captcha-rejection phrases live beside `SUBMIT_CONTROL` in `jobs/submit.py`
    as `CAPTCHA_REJECTED`.
  - Seeded with Lever's "There was an error verifying your application".
  - Generic "verifying your application/submission", "captcha (verification)
    failed", "please complete the captcha" and "bot/automated activity
    detected" go in only once a real page shows them (captured with
    `report_page_failure("captcha_rejected", …)`).
  - `os hunt apply --submit` uses the same set, and its outcome table gains a
    row: **captcha rejected → parks, never presses again; the ATS may have
    cleared the form.**
- **Drive switch.** `os device drive on|off`, plus a toggle on the Hunt devices
  panel. It's stored in the device's config and **defaults off**. Off means
  `/drive` refuses every lease and closes any open one at once. This is the
  kill switch.

### 5. Locks, stop and concurrency (agent side)

- **Per-listing submitted lock** in `state_dir()/drive/locks.json`, keyed by
  listing id and normalized posting URL.
  - **States:** `pending` (the press is about to happen or happened with no
    outcome yet), `submitted`, `unconfirmed`.
  - **When it's set:** at `ready_to_submit`; when a confirmation URL or wording
    appears in **any** tab (the agent already sees `Target.targetInfoChanged`);
    and on Ethan's Submitted control.
  - **Checks:** the agent refuses `Page.navigate`, `Page.reload` and every
    `Input` event on a tab whose URL matches a locked listing. It also checks
    the API's application status (`applied` or later = locked) at lease time.
  - Only `held` (validation errors) and the single post-rejection refill
    release it. Today's incident (a fill reopening `/apply` after a `/thanks`)
    is refused at the first `navigate`.
- **Stop is enforced by the device.** These end the lease and close the
  websocket within one message:
  - the Hunt page's Stop;
  - `os device drive off`;
  - any live-view pointer or key input from Ethan ("he touches it, the bot lets
    go");
  - lease expiry; and
  - the tab closing or navigating off the lease host.

  The client also checks its own stop flag before each action, but correctness
  doesn't depend on it.
- **One driver per device, no driver during a page run.** A page run started
  while a lease is open ends the lease first.

### 6. Security of the channel

| `/cdp` today (`10e84a0`) | `/drive` |
|---|---|
| whole browser: all tabs, cookies, Google session | one tab per lease, allowlisted methods, ATS hosts only |
| always on | off by default; per-device switch on the Hunt page |
| no record | audit log: `state_dir()/drive/audit.jsonl` has time, lease, method, a param summary (lengths for `insertText`/keys, never values), and refusals. Rotated at 5 MB like `memory.log` |
| token only | token only, no Origin, plus the lease's listing checked against the API |

The token is still Ethan's Supabase JWT (about 1 hour). The box gets it the way
the `os` CLI does today; no new credential is created.

### 7. Docs (same change)

- **`workers/README.md`:**
  - `drive.py` and `drive_client.py`.
  - The allowlist, locks, switch and audit log.
  - Remove `/cdp`.
- **`docs/jobs-cli.md`:** `os device drive on|off`, and the
  captcha-rejected outcome.

## Left to the builder

- The exact jitter, curve and timing distributions within the stated ranges.
- How the AX-to-field mapping reuses `jobs/` field kinds.
- The Hunt page banner's component placement.
- The live view keeps working during a lease: it shares the tab and its
  `Page`/`Input` session. The builder makes sure the two don't fight over
  `Emulation` overrides; the stream's override stays the only one.

## Calls I made (check these)

- **Remove `/cdp` rather than keep it behind the switch.** Its whole-browser
  reach isn't needed once `/drive` exists.
- **Default press: (a) once, then (b).** Per-host memory skips (a) after a
  recent rejection. Tradeoff: a refused (a) on Lever costs one refill.
- **Intercepted file chooser opened by a real click,** not a bare
  `setFileInputFiles`.
- **Isolated-world reads allowed but logged,** rather than DOM/AX only.
- **The drive switch defaults off.**
- **No schema change.** `awaiting_submit` rides in the heartbeat's `problem`
  text; locks are device-local plus API status.

## Open questions for Ethan

1. **Was your 11-16 Pivotal submit a click in the Hunt live view, or at the PC
   itself?** A live-view click is also a CDP `Input` event. If that passed, (a)
   with the driver detached is likely to pass too. If only a physical click
   passes, (b) means "at the PC" and (a) should be off for Lever.
2. **Should a captcha rejection on a site skip (a) for 7 days?** Or forever, or
   never?
3. **Is a chat message from Grok enough notice for `awaiting_submit`?** Or do
   you also want a Windows toast on the device?
4. **Can drivers fill long essays,** or must essays come from the answer
   library and fact-backed drafts only, as `os hunt apply` does?

## Verification

- **Gates:** `cd workers && uv run ruff check . && uv run mypy src/ && uv run
  pytest -q`. The backend and frontend gates are as usual.
- **Unit tests** (agent with fake Chrome, as in `test_device_server.py`):
  - Refusals:
    - a method off the allowlist;
    - `Runtime.evaluate` outside the isolated world;
    - `navigate` off-host;
    - `navigate`/`Input` on a locked listing;
    - a second lease;
    - a lease during a page run;
    - switch off;
    - an Origin header.
  - Stop: Stop, live-view input, switch off and expiry each close the lease
    within one message.
  - The press: outcomes from fixture pages (Lever `/thanks`, the Lever
    rejection banner, validation errors, no change). Each yields the right
    lock state and never a second press.
  - The client: key events per character and pointer paths, against a recorder.
  - The audit log never contains an `insertText` value.
- **Manual, on EXAMPLEPC:**
  1. Grok fills a Lever posting through `/drive` and the press goes in by (a),
     or is refused and handed off by (b) with one refill.
  2. Ethan's click confirms; the lock is set; a deliberate `navigate` to
     `/apply` afterwards is refused.
  3. A Greenhouse posting goes in by (a).
  4. Pressing Stop on the Hunt page mid-fill frees the tab at once.

## Rollout

1. Ship `/drive` with the switch off, and remove `/cdp`. The device updates on
   its next run (fast-forward plus restart, as today).
2. Ethan turns the switch on for EXAMPLEPC; Grok runs three Lever and two
   Greenhouse applications with Ethan watching.
3. Read the audit log and the outcomes. If (a) never passes on Lever, set Lever
   to (b)-only.
4. Then consider porting the press handoff and captcha-rejected outcome to
   `os hunt apply --submit`.
