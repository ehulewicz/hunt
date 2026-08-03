# Database game plan

## Status — 2026-08-03

- Minimal private `app` schema applied and verified with exactly ten application
  tables.
- Google-only Auth configuration is represented as code.
- Superseded hosted migration history is reverted and the active local and remote
  history contains one baseline.
- Retired application schemas and the old Storage bucket are absent.
- `anon` and `authenticated` have no application-table grants.
- Google Auth configuration is ready to push, but the required GCP OAuth client ID
  and secret do not yet exist in Secret Manager.

## Next

1. Create the Google OAuth client, add its two Secret Manager values, push Auth
   configuration, verify identity linking, and revoke legacy sessions.
2. Build Jobs operations against the existing tables without adding abstractions.
3. Add document Storage only when upload and versioning are implemented.
4. Add Calendar and Tasks schema only after the Jobs milestone works end to end.
