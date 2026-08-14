# Database current state

Last verified: 2026-08-13

## Milestones

- [x] Maintain hosted Supabase PostgreSQL as the only product database with a
  private `app` schema and no `anon` or `authenticated` table grants.
- [x] Keep Google-only Auth configuration in `database/config.toml`.
- [x] Extend existing Jobs tables for complete profile facts, inclusive source
  metadata, role tags, tailoring diffs, and document lifecycle state.
- [x] Define source-category and normalized-location columns plus indexes for the
  simplified internship listing filters.
- [x] Add revocable, expiring Jobs worker credentials without creating a public
  API or alternate source of truth.
- [x] Define the private `jobs-documents` PDF bucket with a 5 MiB limit.
- [x] Keep approved document content immutable while allowing lifecycle updates.
- [x] Store the Supabase backend-only key and worker JWT key in GCP Secret Manager
  and grant access to the Cloud Run runtime.
- [x] Apply `20260810231802_jobs_vertical_slice.sql` to hosted Supabase.
- [x] Verify the hosted table shape, private bucket configuration, and absence
  of `anon`/`authenticated` grants.
- [x] Revoke public/browser execution of the pre-existing `rls_auto_enable()`
  helper and confirm that finding clears from the Supabase security advisor.
- [x] Review post-deploy advisors: new indexes are unused because the tables have
  not received production traffic; remaining auth warnings concern password/MFA
  configuration in the Google-only product.
- [x] Apply `20260813223148_simplify_speedyapply_listings.sql` to hosted Supabase
  and verify scheduled-sync run `31751692530` populates 1,087 open listings.
- [x] Add `20260814003115_complete_jobs_workflow.sql` to delete the six factual
  Answer rows, enforce one active autofill task per application, and index
  application task/question state.
- [x] Apply the complete-workflow migration in deploy run `31759129535` and
  verify strict-US sync run `31759248462` leaves exactly 440 open rows.
- [x] Confirm the PhD exclusion needs no migration; sync run `31762555087` leaves
  exactly 380 open listing rows under the existing schema.
- [ ] Verify application/history preservation through the authenticated deletion
  and reactivation acceptance flow after this machine is paired.
- [ ] Revoke legacy password sessions after Google acceptance is reconfirmed.
- [ ] Add Calendar and Tasks schema only after Jobs works end to end.
