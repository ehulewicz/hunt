# How the frontend works

The Vercel-hosted Next.js app is the only browser UI. `/` redirects to
`/dashboard`. Dashboard, Jobs, Settings, and Integrations share the persistent
workspace layout and sidebar; a future standalone app can live outside that route
group without inheriting the shell.

Supabase JS owns a persisted browser session and completes Google OAuth with PKCE at
`/auth/callback`. The browser then sends the access token to FastAPI. A `403` causes
an immediate Supabase sign-out and a fixed unauthorized-account message. The
frontend never reads or writes the application database through Supabase.

Jobs navigation is scaffolded, but every Jobs page is an explicit empty state. No
Jobs workflow is implied by the UI.
