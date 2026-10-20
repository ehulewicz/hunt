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

Current scope is deliberately small: Hunt and Integrations are the pages in the
sidebar, and `/` lands on Hunt. Settings is a routed placeholder that is not yet in
the nav.

### Integrations

`/integrations` lists each account Personal OS uses, with columns for the
account, what access it has, and its status: the Google identity that signs into
this workspace, the Cloud Run API, and the Gmail inbox the worker uses for ATS
account walls. The inbox address is the `contact.email` fact, read from
`GET /v1/jobs/facts`. Its access, read-only IMAP over an app password plus Google
sign-in in the automation Chrome, is checked per device by `os doctor`, not by
this page.

### Hunt

`/hunt` is the primary Hunt control. It lists your devices from Cloud Run
(`GET /v1/jobs/devices`, refreshed every 30 seconds): name, online or last seen,
ready or the failing doctor check, "running local changes at <commit>" when the
checkout has its own edits, and the run a device is busy with. Picking an online
device opens its session (`use-device-session.ts`): the run on the left, its Chrome
on the right.

The left pane starts a run (a board number, an id prefix, or a posting URL; or
"Next on board"), and drives it with Save, Submitted, Skip, and Stop, the page's
Ctrl+S, Ctrl+A, Ctrl+D, and Ctrl+C. The run's console arrives as styled spans and
is drawn with token classes (`log-green`, `log-dim`, …), never a raw color. A run
the page started sends its drafted cover letter here to edit; "Use this letter"
sends it back and the run continues. The audit's "Left for you" list arrives as
structured gaps, and a fact gap takes its value inline: the run stores the fact and
fills the field. A run started in a terminal shows here with working controls; its
letter stays in VS Code.

The right pane is the device's Chrome tab, sticky beside the run while the document
scrolls. Frames are JPEGs over the session's WebSocket; a click, wheel turn, or key
on the focused view is sent as fractions of the frame and the DOM's own key fields,
and the agent does the mapping. Ctrl+V pastes this computer's clipboard. A tab
strip switches tabs. A device this browser cannot reach says "Can't reach <name>.
Is this browser on your tailnet?"; a second window takes the session over from the
first.

Jobs otherwise runs through the `os hunt` CLI, see
[`../docs/jobs-cli.md`](../docs/jobs-cli.md).

## Rules

These are binding, not suggestions.

- Use the governed grayscale and blue-gray tokens in `src/styles/tokens.css`. No
  raw hex colors and no gradients outside that file — `npm run check:ui` fails the
  build on either.
- Prefer flat bordered lists and 2–4px corners. Shadows belong only on transient
  overlays.
- The document owns vertical scrolling; avoid nested vertical scroll regions.
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
src/components/       workspace.tsx is the auth gate, app-shell.tsx the sidebar
                      layout, plus account-menu and empty-page. hunt-devices,
                      hunt-run, and hunt-live are the Hunt page's three parts;
                      use-device-session.ts is its connection state.
src/lib/              api.ts (the Cloud Run fetch layer), device.ts (the device
                      fetch and WebSocket layer), and supabase.ts (browser client).
src/styles/           tokens.css is the single source of color and spacing values;
                      base.css and app.css consume it.
tests/                Vitest + Testing Library component tests (jsdom).
e2e/                  Playwright specs for auth, shell, and the Hunt page, whose
                      device socket is stubbed with routeWebSocket.
scripts/              check-ui-policy.mjs — the raw-color and gradient ban.
```
