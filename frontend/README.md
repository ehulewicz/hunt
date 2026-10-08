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
a single `main` content region. `src/lib/api.ts` is the only place `fetch` is
called; it resolves `NEXT_PUBLIC_API_URL` and throws a typed `ApiError` on any
non-OK response.

Required browser environment: `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_API_URL`.

Current scope is deliberately small: Dashboard is the only page with content, while
Settings and Integrations are routed placeholders and are not yet in the sidebar
nav. Jobs has no browser UI — the active Jobs path is the `os hunt` CLI, see
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
- Reuse the persistent workspace layout for Dashboard, Jobs, Settings, and
  Integrations.

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
                      layout, plus account-menu and empty-page.
src/lib/              api.ts (the only fetch layer) and supabase.ts (browser client).
src/styles/           tokens.css is the single source of color and spacing values;
                      base.css and app.css consume it.
tests/                Vitest + Testing Library component tests (jsdom).
e2e/                  Playwright specs for auth and shell behavior.
scripts/              check-ui-policy.mjs — the raw-color and gradient ban.
```
