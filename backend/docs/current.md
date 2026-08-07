# Backend current state

Last verified: 2026-08-03

## Milestones

- [x] Implement minimal health, identity, and bootstrap endpoints.
- [x] Enforce JWT signature, issuer, audience, UUID, and email.
- [x] Archive old domain routers and local/demo behavior.
- [x] Deploy the minimal API to Cloud Run and pass hosted health and
  unauthenticated-rejection checks.
- [ ] Pass the first end-to-end bootstrap using a Google-issued Supabase
  session.
- [ ] Add Jobs profile editing after the assisted first profile is approved.
- [ ] Add GitHub listing ingestion and Jobs CRUD in ordinary internal modules.
- [ ] Add worker claim/lease endpoints when the foreground worker is
  implemented.
- [ ] Add Google integration OAuth only with the Calendar milestone.
