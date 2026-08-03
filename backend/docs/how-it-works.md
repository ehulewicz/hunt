# How the backend works

The Cloud Run service validates every bearer token against the hosted Supabase
JWKS. Authentication and authorization are separate: an invalid token is `401`,
while a valid token whose UUID or verified email differs from the configured
single account is `403`.

The API opens short PostgreSQL transactions and runs `SET LOCAL ROLE
personal_os_api` before touching the private `app` schema. `POST /v1/bootstrap`
creates the authenticated user's Jobs profile row if it is absent. No request
accepts a user ID.

Public surface:

- `GET /health` checks database readiness without returning internals.
- `GET /v1/me` returns the authenticated Google identity.
- `POST /v1/bootstrap` idempotently creates the minimal Jobs profile.
