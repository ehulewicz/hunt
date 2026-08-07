# Database current state

Last verified: 2026-08-07

## Milestones

- [x] Apply and verify the minimal private `app` schema with exactly ten
  application tables.
- [x] Represent Google-only Auth configuration in `database/config.toml`.
- [x] Reconcile hosted migration history to one active baseline.
- [x] Remove retired application schemas and the old Storage bucket.
- [x] Verify `anon` and `authenticated` have no application-table grants.
- [ ] Create the Google OAuth Web client in GCP project `example-gcp-project`.
- [ ] Add enabled Secret Manager versions for
  `personal-os-google-oauth-client-id` and
  `personal-os-google-oauth-client-secret`.
- [ ] Run the Deploy workflow to apply `database/config.toml` with `supabase
  config push`.
- [ ] Verify hosted Auth reports Google enabled and global signup disabled.
- [ ] Verify Google identity linking, then revoke legacy password sessions.
- [ ] Build Jobs operations against the existing tables without adding
  abstractions.
- [ ] Add document Storage only when upload and versioning are implemented.
- [ ] Add Calendar and Tasks schema only after Jobs works end to end.

## Verified hosted drift

On 2026-08-07, `/auth/v1/settings` reported `google: false` and
`disable_signup: false`. The checked-in config says Google enabled and global
signup disabled. Both Google OAuth secrets are absent from Secret Manager.
Deploy run `31194687918` failed while resolving hosted configuration, before
`supabase config push`, and skipped API deployment. Hosted Auth must not be
treated as matching `database/config.toml` until the checklist above passes.
