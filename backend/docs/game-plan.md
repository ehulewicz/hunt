# Backend game plan

## Status — 2026-08-03

- Minimal health, identity, and bootstrap endpoints implemented.
- JWT signature, issuer, audience, UUID, and email enforcement implemented.
- Old domain routers and local/demo behavior archived.
- The minimal API is deployed on Cloud Run; hosted health and unauthenticated
  rejection checks pass.

## Next

1. Add Jobs profile editing after the assisted first profile is approved.
2. Add GitHub listing ingestion and Jobs CRUD in ordinary internal modules.
3. Add worker claim/lease endpoints when the foreground worker is implemented.
4. Add Google integration OAuth only with the Calendar milestone.
