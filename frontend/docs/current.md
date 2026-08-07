# Frontend current state

Last verified: 2026-08-07

## Milestones

- [x] Move the preserved visual shell to `frontend/`.
- [x] Implement Google-only login and the PKCE callback.
- [x] Implement the persistent workspace routes and six explicit Jobs
  placeholders.
- [x] Remove password signup and the general Profile screen.
- [x] Point the production Vercel project at `frontend/` and serve the current
  route structure at `https://personal-os-ten-kappa.vercel.app`.
- [ ] Complete a hosted Google login and logout acceptance pass.
- [ ] Complete the one-time assisted Jobs Profile setup.
- [ ] Replace Jobs placeholders one flow at a time, starting with GitHub
  listings.
- [ ] Add Calendar and unified Tasks only after Jobs works end to end.

## Active blocker

The frontend is sending the correct provider and callback, but hosted Supabase
Auth reports Google disabled. The OAuth client, GCP secrets, and Auth config push
are tracked in the root and database `docs/current.md` files.
