# Database current state

Last verified: 2026-08-07

## Milestones

- [x] Apply and verify the minimal private `app` schema with exactly ten
  application tables.
- [x] Represent Google-only Auth configuration in `database/config.toml`.
- [x] Reconcile hosted migration history to one active baseline.
- [x] Remove retired application schemas and the old Storage bucket.
- [x] Verify `anon` and `authenticated` have no application-table grants.
- [x] Create the Google OAuth Web client in GCP project `example-gcp-project`.
- [x] Add enabled Secret Manager versions for
  `personal-os-google-oauth-client-id` and
  `personal-os-google-oauth-client-secret`.
- [x] Run the Deploy workflow to apply `database/config.toml` with `supabase
  config push`.
- [x] Verify hosted Auth reports Google enabled and global signup disabled.
- [ ] Verify Google identity linking, then revoke legacy password sessions.
- [ ] Build Jobs operations against the existing tables without adding
  abstractions.
- [ ] Add document Storage only when upload and versioning are implemented.
- [ ] Add Calendar and Tasks schema only after Jobs works end to end.

## Verified hosted state

On 2026-08-07, Deploy run `31198525381` successfully applied
`database/config.toml` and redeployed the API. Hosted Auth reports Google
enabled, email auth disabled, and global signup disabled. Its Google authorize
endpoint redirects to Google. Interactive identity-linking acceptance and
legacy-session revocation remain incomplete.
