# Database current state

Last verified: 2026-08-10

## Milestones

- [x] Maintain hosted Supabase PostgreSQL as the only product database with a
  private `app` schema and no `anon` or `authenticated` table grants.
- [x] Keep Google-only Auth configuration in `database/config.toml`.
- [x] Extend existing Jobs tables for complete profile facts, inclusive source
  metadata, role tags, tailoring diffs, and document lifecycle state.
- [x] Add revocable, expiring Jobs worker credentials without creating a public
  API or alternate source of truth.
- [x] Define the private `jobs-documents` PDF bucket with a 5 MiB limit.
- [x] Keep approved document content immutable while allowing lifecycle updates.
- [x] Store the Supabase backend-only key and worker JWT key in GCP Secret Manager
  and grant access to the Cloud Run runtime.
- [ ] Apply `20260810231802_jobs_vertical_slice.sql` to hosted Supabase.
- [ ] Verify the hosted table shape, bucket policy, role grants, and Supabase
  security/performance advisors after deployment.
- [ ] Revoke legacy password sessions after Google acceptance is reconfirmed.
- [ ] Add Calendar and Tasks schema only after Jobs works end to end.
