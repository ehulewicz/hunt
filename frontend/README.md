# Frontend

The Vercel-hosted Next.js 16 app. It is the only browser UI and the only place
browser-side Supabase Google authentication lives. It holds **no business logic**
and never reads or writes application tables or private Storage directly — product
data is reachable only through the Cloud Run FastAPI service.

## How it works

Supabase JS owns the Google PKCE browser session. `src/app/login` starts the OAuth
flow, `src/app/auth/callback` completes it, and `src/components/workspace.tsx`
gates every authenticated route: it reads the Supabase session, calls
`authenticateAndBootstrap` against FastAPI, and on `403` signs out locally and
redirects to `/login?reason=unauthorized`. Everything below that gate gets the
account and access token through the `useAccount` / `useAccessToken` context, and
sends the access token as a bearer header on each API call.

`src/app/(workspace)/` is the persistent shell: a sidebar with the account menu and
a single `main` content region. `src/lib/api.ts` is the only place Cloud Run is
called; it resolves `NEXT_PUBLIC_API_URL` and throws a typed `ApiError` on any
non-OK response. `src/lib/device.ts` is the only place a device's agent is called,
over the tailnet: its fetch and WebSocket take a fresh Supabase access token on
every connect and refuse any address that is not `https://<name>.ts.net`, because
the token travels with every request.

Required browser environment: `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_API_URL`.

The sidebar links Hunt (`/hunt`), Resume (`/resume/edit`), and Library
(`/library`); `/` lands on Hunt. Integrations and Settings open from the account
menu; Settings is still a routed placeholder.

`/resume/edit` edits the career record as its JSON document: save parses,
PUTs, and reloads what the API rendered back — the record gets stable entry
ids — and a compile refusal from the PDF build shows inline. `/library` edits
the narrative library: entries grouped by section with read-only prompt and
guidance, a per-entry answer box with a live word count, and an unanswered
filter; saving one entry PUTs its full write body under its code.

Both use `document-editor.tsx`, the shared document editor: a labelled
textarea (monospace where the text is structured), a save button, inline
refusals and a saved notice. Its optional Ask Claude panel calls a device's
agent — `lib/device.ts askClaude` posts `{surface, instruction, document}` to
`/assist` — and shows the reply as a summary plus a diff (lines for monospace
text, words otherwise) to Accept or Discard; accepting runs the caller's save
path, and nothing is saved on its own. With no online ready device the panel
says so; on an empty library answer it asks for a first draft first.

The Hunt run's cover letter is the same editor: "Use this letter" sends the
text through the run, and its Ask Claude proposal only replaces the draft.

### Public files

`/resume` serves the published resume PDF directly, without Google sign-in.
A Next.js rewrite proxies `GET /v1/public/resume.pdf` on the API —
`RESUME_PDF_URL` can point it elsewhere, and it defaults to
`NEXT_PUBLIC_API_URL` — while keeping `/resume` in the address bar. The backend
sends the inline disposition itself, so the browser's PDF viewer opens it with
the applicant's name as the download filename. No copy is committed here; the
published PDF refreshes when the career record is saved. Workspace routes
remain authenticated.

`/research/tiny-object-tracking` serves `public/Tiny_Object_Tracking.pdf` the
same way: a rewrite plus an inline disposition naming the download after the
paper's title. That PDF is a fixed copy of the Sample Lab paper, not generated
from anything, so it is replaced only by committing a new file.

### Integrations

`/integrations` lists each account Personal OS uses, with columns for the
account, what access it has, and its status: the Google identity that signs into
this workspace, the Cloud Run API, and the Gmail inbox the worker uses for ATS
account walls. The inbox address is the `contact.email` fact, read from
`GET /v1/jobs/facts`. Its access, read-only IMAP over an app password plus Google
sign-in in the automation Chrome, is checked per device by `os doctor`, not by
this page.

### Hunt

`/hunt` opens an overview, without connecting to a device or streaming Chrome.
It reads application history, the answer inbox, facts, and the board total through
FastAPI. Totals show applications sent (including later outcomes), pending
applications, and listings on the board. Failed overview loads show an unavailable
state, or the last successful overview, rather than zero counts. Overview data
and devices refresh every 30 seconds and on return
from an application.

The overview starts an application by board number, posting id prefix, or direct
posting URL, or chooses "Next on board". A ready online device is selected by
default; the header's device picker shows readiness, local changes, and current
runs. A pending application's Resume action uses its stored posting id. A run
already active on a device opens without starting another one, including runs
started in a terminal. Starting a busy or unready device is disabled.

The answer panel switches between Inbox and Library. Inbox separates Questions,
Facts to verify, Writing, Pattern checks, and Unsolved. Writing also lists open
prose gaps — a long-form question the library covers only partly or not at all —
with a link into the Library page and a Resolve action. Facts to verify comes from
`/v1/jobs/inbox`: unverified facts, missing facts with the question that needed them,
and contradicted facts with replay counts. Unsolved shows failures the run could
not fix, including their notes and occurrence counts. Cause tags use recorded
origins, fact reasons, replay disagreements, or failure categories where known.
Unbound questions can be linked to an existing fact or a new typed fact. Empty
text is saved unchanged; lists use JSON, including `[]`, and both display as None.
Writing is one text, with model and previous-answer spans tinted separately and
the user's and library's text plain; a library span's hover names its entry
code. Edit answer switches that text into the editor; highlights
are not reused against edited text. Older writing without provenance says so.
The current API leaves provenance unchanged after a text edit; reopening edited
writing can therefore show stale offsets until the API clears or recomputes them.
Writing can be saved as an unapproved draft, approved for reuse, or discarded.
Failed pattern checks come before suggestions and show how many accepted answers
the replay changed. Patterns show their fact's current value and can be accepted
or rejected. Inbox review requests send `origin: "inbox"`; direct Library edits
send `origin: "you"`. The prose DELETE endpoint does not record a supplied origin.

Library loads on opening and refreshes every 30 seconds, independently of the
overview. Its searchable Questions, Facts, Writing, and Patterns categories read
`/v1/jobs/questions`, `/facts`, `/prose`, and `/patterns`. It includes approved
writing, verified facts, and all pattern statuses, including rejected and retired
records, plus repaired and validated patterns. Facts and writing use the same
editors as Inbox; questions and patterns expose their stored details. A stored
fact's Delete action asks for confirmation, then refreshes Library and Inbox.
Deleting an override lets the career-derived value apply again. The facts API
lists stored rows only, so computed-only facts have no Delete action: change
career-derived facts in [the resume editor](/resume/edit), and other derived
facts in code.
Each item's
history loads from `/v1/jobs/changes`,
showing when, who, action, before and after, and replay verification. Code lists
code changes newest first, including commit, cause, question, summary, attempts,
files, live probe, push status, and diff when recorded. History refreshes every
30 seconds while open. Failed loads offer retry and retain the last successful
records; empty history says no changes were recorded.

A successful start or an explicit Open action replaces the overview with an
application workspace. Its default split gives answers one third and Chrome two
thirds; the header can switch to equal widths. The left panel holds the current
application's Save, Submitted, Skip, and Stop controls, "To finish" questions,
a cover letter tab, and a collapsible run log. Live fixes appear alongside
"Left for you", restoring from the run snapshot and updating from device socket
messages. Each shows the question, cause, state,
attempt out of three, and note, including the commit note for a kept fix.
Fact gaps accept a value inline, which the run stores and fills. The cover letter
is edited here and "Use this letter" sends it back; a terminal run's letter comes to the page too. Unsent letter
edits and unsent fact inputs survive a trip to the overview during the page's
lifetime and remain scoped to their run. Returning to the
overview closes only the browser's device session, not the application run.

The device's Chrome occupies the right panel. Its tabs scroll horizontally;
frames are JPEGs over an authenticated WebSocket. Clicks, wheel turns, and keys on
the focused view go to Chrome, and Ctrl+V pastes this computer's clipboard.
On connect, reconnect, and resize, the page sends the panel's size and pixel
density, and the agent lays the tab out at that shape, never narrower than the
panel, so text is never shown past 1x, and wider when the page needs zooming
out to fit. A frame is fitted to fill the panel. The panel itself never
scrolls, and every wheel turn scrolls Chrome. A frame of another shape is fitted inside the panel. A device this browser
cannot reach explains the tailnet connection and offers a retry. A second window
can take over a session. Run controls and answer submission are disabled while
disconnected. The console uses token classes for its styled spans, follows new
lines at the bottom, and holds still when scrolled up.

The overview keeps compact totals, the application launcher, and Applications
on the left; Inbox and Library fill the right column from top to bottom. On desktop,
Hunt fits the viewport; application lists, the inbox, the answer panel, the log,
and Chrome have bounded scrolling. Narrow screens use document scrolling and
stack the workspace panels on phones. The rest of the app uses the
same compact icon rail and ember theme.

Jobs otherwise runs through the `os hunt` CLI, see
[`../docs/jobs-cli.md`](../docs/jobs-cli.md).

## Rules

These are binding, not suggestions.

- Use the governed charcoal and ember tokens in `src/styles/tokens.css`. No
  raw hex colors and no gradients outside that file — `npm run check:ui` fails the
  build on either.
- Use flat bordered lists, 6px control corners, and 12px panel corners. The active
  application card owns the ember gradient. Shadows belong only on transient overlays.
- The document owns vertical scrolling outside Hunt. Hunt is a desktop viewport
  workspace with bounded, independently scrollable lists, answers, log, and Chrome.
- Next.js and TypeScript are presentation and browser-auth concerns only. Business
  logic belongs in Python.
- Reuse the persistent workspace layout for Hunt, Settings, and Integrations.

## Run and verify

```bash
cd frontend && npm ci
npm run dev                  # http://localhost:3000
```

Run the full gate after any UI change — it is exactly what CI runs:

```bash
npm run verify               # lint + typecheck + test + check:ui + build
```

When browser behavior changes, also run the Playwright suite. It starts its own
dev server with stub environment values:

```bash
npx playwright install --with-deps chromium   # first time only
npm run test:visual
npm run test:visual:update                    # only to accept new snapshots
```

Individual steps are `npm run lint`, `npm run typecheck`, `npm test`, and
`npm run check:ui`.

## Structure

```text
src/app/              App Router. (workspace)/ is the authenticated shell group;
                      login/ and auth/callback/ are the unauthenticated OAuth pair.
                      fonts/ holds the self-hosted Instrument Sans Latin subset
                      from the supplied design reference and its OFL license.
src/components/       workspace.tsx is the auth gate, app-shell.tsx the sidebar
                      layout, plus account-menu and empty-page. document-editor.tsx
                      is the shared editor with the Ask Claude panel; resume-editor
                      and library-editor are the document pages. hunt-dashboard and
                      hunt-inbox own the overview; hunt-library, hunt-history, and
                      hunt-review provide the saved-answer library and shared editors;
                      hunt-devices, hunt-run, and
                      hunt-live own device selection and the application workspace;
                      use-device-session.ts is its connection state.
src/lib/              api.ts (the Cloud Run fetch layer), device.ts (the device
                      fetch and WebSocket layer), diff.ts (proposal diffs), and
                      supabase.ts (browser client).
src/styles/           tokens.css is the single source of color and spacing values;
                      base.css, app.css, and hunt.css consume it.
tests/                Vitest + Testing Library component tests (jsdom).
e2e/                  Playwright specs for auth, shell, and the Hunt page, whose
                      device socket is stubbed with routeWebSocket.
scripts/              check-ui-policy.mjs — the raw-color and gradient ban.
public/               Site icon and the research paper PDF. The resume PDF is
                      served from the API, not committed here.
```
