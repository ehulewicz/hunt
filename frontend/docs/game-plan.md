# Frontend game plan

## Status — 2026-08-03

- Preserved visual shell moved to `frontend/`.
- Google-only login and PKCE callback implemented.
- Persistent workspace routes and six Jobs placeholders implemented.
- Password signup and the general Profile screen removed.
- Production Vercel project now builds from `frontend/` and serves the new route
  structure at the canonical Personal OS URL.
- The hosted Google provider cannot complete sign-in until its GCP OAuth client is
  created and pushed to Supabase Auth.

## Next

1. Complete one hosted Google login and logout acceptance pass.
2. Complete the one-time assisted Jobs Profile setup.
3. Replace Jobs placeholders one flow at a time, starting with GitHub listings.
4. Add Calendar and unified Tasks only after Jobs works end to end.
