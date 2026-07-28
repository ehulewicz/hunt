# Deployment — Personal OS

_Last updated: 2026-07-27._

Production topology:

```text
Browser ──▶ Vercel (Next.js dashboard)
   │              │
   │ Supabase JWT │ Supabase JWT
   ▼              ▼
Supabase Auth   FastAPI on Cloud Run ──▶ Supabase Postgres
(hosted)        (personal-os-api)        (Supavisor session pooler)
```

- **Supabase (hosted)** — authoritative Postgres 17, Auth, and Storage.
- **Cloud Run** — the FastAPI service, deployed by GitHub Actions on push to
  `main`. `--allow-unauthenticated` at the IAM layer; application-level
  Supabase JWT verification is the real auth boundary.
- **Vercel** — hosts `apps/dashboard` through Vercel's GitHub integration
  (root directory `apps/dashboard`), auto-deploying on push to `main`.
- **Cloudflare** — not used yet. It has no role in authentication; it becomes
  relevant only for custom-domain DNS, WAF/rate limiting, or tunnels later.
- **Jobs and Learning execution** — their remaining loopback/web-process
  dependencies are deployment gaps. They are not supported product paths until
  deployed behind the same hosted boundary.

Production dashboard: `https://personal-os-ten-kappa.vercel.app`

Production API: `https://personal-os-api-dyzemjumta-ue.a.run.app`

## Secrets: GCP Secret Manager is the origin

Every static app-level secret originates in **GCP Secret Manager** (project
`example-gcp-project`). Nothing else is a source of truth. Cloud Run mounts
secrets at runtime, CI reads them through Workload Identity Federation, and
developer `.env` files—when needed for verification—are disposable copies.

### What lives where

| Secret (Secret Manager) | Consumed by | As |
|---|---|---|
| `personal-os-api-database-url` | Cloud Run, CI | `PERSONAL_OS_API_DATABASE_URL`; CI parses the project ref + DB password out of it |
| `personal-os-api-oauth-token-encryption-key` | Cloud Run | `PERSONAL_OS_API_OAUTH_TOKEN_ENCRYPTION_KEY` |
| `personal-os-supabase-access-token` | CI (migrate job) | `SUPABASE_ACCESS_TOKEN` for `supabase link` / `db push` |

Non-secret values are **not** stored as secrets: the Supabase URL and project
ref derive from the ref `exampleprojectref`; the publishable key is public and
fetched from the Supabase management API (`supabase projects api-keys`); the
Cloud Run URL comes from `gcloud run services describe`.

Calendar OAuth, Gemini, and the GitHub App are the next hosted integrations.
Each app-level credential must be created in Secret Manager and mounted into
Cloud Run before its Dashboard control is enabled.

### Distribution (each target reads from the origin, never the reverse)

| Target | Mechanism | Script |
|---|---|---|
| **Cloud Run** | `--set-secrets` mounts Secret Manager versions natively | (provisioning) |
| **GitHub Actions** | WIF → `gcloud secrets versions access` in the `migrate` job | (in `deploy.yml`) |
| **Vercel** | `vercel env` set from the origin (public `NEXT_PUBLIC_*` config) | `scripts/secrets/push-vercel.sh` |
| **Developer verification** | rendered gitignored `.env` files from `gcloud` | `scripts/secrets/sync-local.sh` |

### OAuth: three categories, handled differently

- **App-level client credentials** (Google/Microsoft calendar client id+secret,
  future providers) — Secret Manager secrets once deployed, distributed like any
  other secret.
- **Per-user connection tokens** (linking Google Calendar/Tasks and future
  providers) — stored encrypted in hosted Postgres. Secret Manager holds the
  `oauth_token_encryption_key` that protects them.
- **Site login providers** (Google/GitHub "sign in") — applied in the Supabase
  dashboard (never `supabase config push`), so distribution is manual.

### Agent access

Claude/Codex read secrets through your authenticated `gcloud` — no separate
credential. `gcloud` as you can read every secret in the project; if you want
per-secret scoping for automation, create a dedicated service account with
`secretAccessor` on only the secrets it needs and use its key.

## Authentication flow (production)

1. Browser signs in via Supabase Auth (email/password) → receives an ES256 JWT
   signed by the project's asymmetric key.
2. Every API call carries `Authorization: Bearer <token>`.
3. FastAPI (`PERSONAL_OS_API_JWT_MODE=jwks`) verifies signature against
   `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`, plus
   issuer/audience/expiry, then derives the owner context in the database
   (`SET LOCAL ROLE personal_os_api`, forced RLS).

## CI pipeline (`.github/workflows/deploy.yml`)

Push to `main` touching `services/api/**`, `modules/**`, or
`supabase/migrations/**`:

1. **migrate** — authenticates to GCP via Workload Identity Federation, reads
   the Supabase access token and database URL from Secret Manager (parsing the
   project ref + DB password out of the URL), then Supabase CLI 2.109.1 links
   the hosted project and runs `supabase db push` (migrations only; synthetic
   seeds are isolated test fixtures and never production data).
2. **deploy-api** — authenticates to GCP via Workload Identity Federation,
   builds the image from the repo root (`services/api/Dockerfile` — root
   context is required because the API depends on `modules/*` path packages),
   pushes `:$GITHUB_SHA` + `:latest` to Artifact Registry, and deploys the SHA
   tag to Cloud Run. Runtime env vars/secrets live on the Cloud Run service
   and carry over between revisions.

The frontend deploys separately via Vercel's own GitHub integration.

### Repo configuration the workflow expects

| Kind | Name | Value |
|---|---|---|
| Variable | `GCP_PROJECT_ID` | GCP project id |
| Variable | `GCP_REGION` | e.g. `us-east1` |
| Variable | `GCP_WIF_PROVIDER` | `projects/<number>/locations/global/workloadIdentityPools/github/providers/github-provider` |
| Variable | `GCP_DEPLOYER_SA` | `personal-os-deployer@<project>.iam.gserviceaccount.com` |

**No GitHub secrets are required.** Both jobs authenticate to GCP via WIF, and
the `migrate` job resolves the Supabase credentials from Secret Manager at
runtime. The deployer service account (`GCP_DEPLOYER_SA`) needs
`roles/secretmanager.secretAccessor` on `personal-os-supabase-access-token` and
`personal-os-api-database-url`. See "Secrets: GCP Secret Manager is the origin"
above.

## Cloud Run service configuration

Service `personal-os-api`, runtime service account `personal-os-api@…`,
1 CPU / 512Mi, min 0 / max 2 instances. Env vars (set once with
`--env-vars-file`; **never** `--set-env-vars`, which splits the JSON CORS list
on commas):

```yaml
PERSONAL_OS_API_ENVIRONMENT: "production"
PERSONAL_OS_API_SUPABASE_URL: "https://<ref>.supabase.co"
PERSONAL_OS_API_JWT_MODE: "jwks"
PERSONAL_OS_API_JWT_AUDIENCE: "authenticated"
PERSONAL_OS_API_CORS_ALLOWED_ORIGINS: '["https://personal-os-ten-kappa.vercel.app"]'
PERSONAL_OS_API_CALENDAR_OAUTH_REDIRECT_URI: "https://personal-os-ten-kappa.vercel.app/integrations"
PERSONAL_OS_API_DATABASE_POOL_MIN_SIZE: "1"
PERSONAL_OS_API_DATABASE_POOL_MAX_SIZE: "4"
PERSONAL_OS_API_DATABASE_POOL_TIMEOUT_SECONDS: "10"
PERSONAL_OS_API_LOG_LEVEL: "INFO"
```

Secret Manager secrets (mounted with `--set-secrets`):

| Secret | Env var |
|---|---|
| `personal-os-api-database-url` | `PERSONAL_OS_API_DATABASE_URL` |
| `personal-os-api-oauth-token-encryption-key` | `PERSONAL_OS_API_OAUTH_TOKEN_ENCRYPTION_KEY` |
| `personal-os-api-gemini-api-key` | `PERSONAL_OS_API_GEMINI_API_KEY` |
| `personal-os-api-github-app-private-key` | `PERSONAL_OS_API_GITHUB_APP_PRIVATE_KEY` |

The GitHub App ID and slug are non-secret Cloud Run environment variables:
`PERSONAL_OS_API_GITHUB_APP_ID` and `PERSONAL_OS_API_GITHUB_APP_SLUG`.

### Database connection: Supavisor session pooler, not direct

Cloud Run egress is IPv4-only and hosted Supabase direct connections
(`db.<ref>.supabase.co:5432`) are IPv6-only, so the API connects through the
**Supavisor session pooler** (port 5432):

```
postgresql://postgres.<ref>:<password>@<pooler-host>:5432/postgres?sslmode=require
```

Session mode — not transaction mode (port 6543) — because psycopg's named
prepared statements and the long-lived `AsyncConnectionPool` need a dedicated
backend per connection. Backend usage is bounded: pool max 4 × max 2
instances = ≤ 8 sessions. `create_pool` verifies connections before reuse
(`check=check_connection`, `max_lifetime=1800`) because Supavisor drops idle
sessions.

## Hosted Supabase settings (dashboard-managed)

`supabase/config.toml` configures only the isolated test stack; never
`supabase config push`. Hosted auth settings live in the Supabase dashboard:

- **JWT signing keys**: asymmetric ES256 key created and promoted to current
  (required by `jwt_mode=jwks`; legacy HS256 tokens are rejected). Promote
  **before** first login.
- **Site URL**: `https://personal-os-ten-kappa.vercel.app`.
  **Redirect URLs**: that production origin only.
- **Email confirmations: off** (the dashboard signup flow expects an
  immediate session; avoids SMTP setup).
- **Signups: disabled** after the owner account exists (single-user system
  with a public auth endpoint). Re-enable temporarily to add an account.

## Vercel

Project imported from GitHub, root directory `apps/dashboard`, framework
Next.js. Environment variables:

```
NEXT_PUBLIC_SUPABASE_URL=https://<ref>.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
NEXT_PUBLIC_PERSONAL_OS_API_URL=https://personal-os-api-dyzemjumta-ue.a.run.app
NEXT_PUBLIC_LEETCODE_BRIDGE_EXTENSION_ID=<installed-extension-id>
```

`NEXT_PUBLIC_JOBS_URL` and `NEXT_PUBLIC_LEARNING_RUNNER_URL` remain unset until
those services have hosted HTTPS endpoints. The Dashboard must not fall back to
loopback URLs. The bridge manifest permits only the exact deployed Dashboard
origin. The publishable key is designed to be public; the database password
and access tokens are not and never reach the frontend.

## Developer verification

`apps/dashboard/.env.stack-hosted` targets hosted Supabase and Cloud Run and is
generated by `scripts/secrets/sync-local.sh`. `npm run dev:hosted` runs a
development client against the real product backend. The repository's local
Supabase and `npm run dev:stack` remain available only for isolated tests and
migration validation; feature acceptance must be repeated against the hosted
path.

## Operations

- **Roll back the API**: `gcloud run services update-traffic personal-os-api
  --region <region> --to-revisions <previous-revision>=100`. Schema rollbacks
  are forward-fix migrations, never destructive edits.
- **Rotate a secret** (general): add a new version in Secret Manager
  (`gcloud secrets versions add <name> --data-file=-`), then propagate the
  copies — `scripts/secrets/push-vercel.sh` and/or `sync-local.sh` — and
  redeploy the affected target (Cloud Run pins versions per revision). CI and
  Cloud Run read the origin directly, so they need no push.
- **Rotate the DB password**: reset it in the Supabase dashboard → add a new
  `personal-os-api-database-url` version with the updated URL
  (`gcloud secrets versions add personal-os-api-database-url --data-file=-`) →
  redeploy (`gcloud run services update personal-os-api`). CI parses the new
  password out of that same secret automatically on its next run.
- **First-time migration push** was manual (`supabase link` + `db push`)
  to catch hosted-specific failures interactively; CI handles subsequent
  pushes. `supabase migration list` shows local/remote applied state.
- **Health**: `GET /livez` (process up) and `GET /readyz` (DB reachable, roles
  granted, core functions present — proves migrations landed).

## Known behaviors that are not bugs

- SQL-editor queries on application tables can return zero rows while the API
  works fine — forced RLS applies to ad-hoc sessions that never set the
  request context.
- `/v1/demo` routes return empty results because synthetic test fixtures are
  not production data.
- Jobs and Learning execution surfaces stay hidden or unavailable until their
  HTTPS services are deployed; a loopback fallback is a bug.
