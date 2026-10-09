# Database

Canonical hosted Supabase configuration and the single active private application
schema. This directory is **deployment input, not a local database stack** — there
is no local Supabase runtime and no seed workflow.

## How it works

Hosted Supabase PostgreSQL 17 is the only product database. Supabase owns `auth`
and `storage`; Personal OS owns one private `app` schema. That schema is absent
from the Data API schemas in `config.toml` and grants no table access to `anon` or
`authenticated`, so it is unreachable from the Data/GraphQL APIs.

FastAPI connects with the managed database credential, opens short transactions,
and assumes the non-login `personal_os_api` role. Every user-owned row references
`auth.users`; the API derives that UUID from a verified JWT and never accepts it in
a request body. There is no RLS, by design, because the product is single-user.

### What the Jobs tables hold

Facts, source listings, recoverably archived applications with
a lifecycle status (`queued`, `applied`, `oa`, `oa_completed`, `interview`, `offer`,
`rejected`, `archived`), immutable versioned documents, approved long-form answers,
application-question event history, synchronous autofill runs, deduplicated failure
issues, and failure occurrences.

An observed history row is identified by run and event index; final rows retain the
submitted or drafted value with step and control context. Autofill never approves
answer-library rows. `jobs_worker_credentials` stores only SHA-256 token hashes plus
scope, expiry, use, and revocation timestamps. Listing source metadata keeps
discovery and direct application URLs separate, records the FAANG+/Quant/general
source section, and stores normalized location arrays for match-any filtering.
Composite, partial, and GIN indexes support newest-first discovery, unanswered
questions, and location overlap. The worker task queue and tailoring audit tables
were dropped when the task worker was retired.

`jobs_facts` holds one row per fact, keyed per user by a dotted name, with a
`jsonb` value whose shape a check constraint ties to `value_type` (`text`,
`boolean`, `number`, `date`, `choice`, `list`, `object`). `sensitive` marks values
to withhold from model calls; `verified_at` records the user's review. Facts that
change with time — completed co-op rotations, graduation term, annual minimum
compensation — are derived by the worker and never stored; `coop.anchor` and
`compensation.minimum` are their stored inputs. The worker picks a form-specific
rendering rather than copying facts per application. `jobs_profile` was seeded
into `jobs_facts` and is no longer read; it is dropped once every fact is
verified.

### Storage buckets

Private `jobs-documents` accepts PDFs up to 5 MiB. Private
`jobs-autofill-artifacts` accepts PNG, HTML, text, and JSON evidence up to 10 MiB.
Only FastAPI holds the backend secret key — the browser and worker never receive
it. Approved document content cannot be mutated or deleted, though
current/archive lifecycle fields can change.

## Run and verify

Migrations are applied only by the `Deploy` GitHub Actions workflow, which symlinks
this directory as `supabase/`, then runs `supabase link`, `supabase db push`, and
`supabase config push` against the linked hosted project. Nothing here is applied
from a developer machine as part of normal work.

```bash
gh workflow run Deploy
gh run watch --exit-status
```

A new migration is a new timestamped file in `migrations/`. Verify the resulting
schema through the backend suite, which is the only consumer:

```bash
cd backend && uv run pytest
```

## Structure

```text
config.toml     hosted Supabase project config: Data API schemas, Auth (Google
                only, signup disabled), Storage limits. Pushed by Deploy.
migrations/     the only runnable schema history, timestamped and append-only.
                `20260802224217_minimal_reset.sql` is the active baseline; later
                files build the Jobs vertical on top of it.
```

Deployment details and secrets: [`../docs/deployment.md`](../docs/deployment.md).
The only client of this schema: [`../backend/README.md`](../backend/README.md).
