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

Facts, the career record, source listings, recoverably archived applications with
a lifecycle status (`queued`, `applied`, `oa`, `oa_completed`, `interview`, `offer`,
`rejected`, `archived`), learned questions, patterns, and prose, the archive,
application-question event history, synchronous autofill runs, deduplicated failure
issues, and failure occurrences.

`jobs_questions`, `jobs_patterns`, and `jobs_prose` are what the system has
learned since the 2026-10-14 restart. A question is a normalized label, one row
per user and key, recorded when first met, with a disposition (`unbound`,
`pattern`, `one_off`, `long_form`). A pattern is a `binding` (one question) or an
`expression` (a regex), always reading one fact key, with a lifecycle status;
at most one binding per question is `active`, and one row exists per question
and fact. Prose is long-form writing tied to the question and application it
was written for, unapproved until reviewed. Answer history and fill events carry
`question_id`, and fill events the `pattern_id` that produced their value.

The archive is the old system, renamed and frozen — the API role can only read
it: `jobs_archive_answers` (the former answer library, 198 rows),
`jobs_archive_profile` (the former profile), and `jobs_archive_documents`. It
serves only the facts session and is dropped after it; no application reads it.
Failure issues double as the repair queue: `stopped_at` (`format` or `strategy`)
says whether Repair can retry one, `candidate` holds the rule a repair found, and
`repaired_at` and `validated_at` date the two steps; a `repaired` or `validated`
issue must carry its candidate. Failure issues the old dispatch left open are
`archived`; a signature that
fails again reopens.

An observed history row is identified by run and event index; final rows retain the
submitted or drafted value with step and control context. Autofill never activates a
pattern or approves prose. `jobs_worker_credentials` stores only SHA-256 token hashes plus
scope, expiry, use, and revocation timestamps. Listing source metadata keeps
discovery and direct application URLs separate, records the FAANG+/Quant/general
source section, and stores normalized location arrays for match-any filtering.
Composite, partial, and GIN indexes support newest-first discovery, unanswered
questions, and location overlap. The worker task queue and tailoring audit tables
were dropped when the task worker was retired.

`jobs_controls`, `jobs_strategies`, and `jobs_fill_events` measure the fill
path. A control is a kind (one of seven) plus a `jsonb` structural attribute
vector, unique per user, and never includes the ATS or the question. A strategy
is a named way of operating a kind, with a precedence and a lifecycle status; the
ten that exist in code are seeded `active`. A fill event is one attempt: the
question's normalized key, the resolver id, the control, the strategy, the ATS
as a dimension only, a value hash, and `stopped_at` (`pattern`, `format`,
`strategy`, or null) with `operated_ok` (true read back, false failed, null not
attempted). `question_key` and `resolver_id` stand in until `jobs_questions` and
`jobs_patterns` exist and add real foreign keys.

`jobs_education`, `jobs_resume_entries`, `jobs_resume_bullets`, and `jobs_skills`
are the career record, the canonical resume. Dates are month-precision `date`s
(the first of the month); an entry's `end_date` is null with a start date for
"Present", and a single-month project stores only `end_date`. Only experience
carries an `employment_type`, so research and projects can never count toward
tenure. One education row per user is `is_primary`, and a bullet can never hold a
semicolon. `jobs_archive_documents` is the former `jobs_documents` — a 2026-08
copy of the resume and two tailored resumes — renamed and frozen: the API role
can only read it.

`jobs_facts` holds one row per fact, keyed per user by a dotted name, with a
`jsonb` value whose shape a check constraint ties to `value_type` (`text`,
`boolean`, `number`, `date`, `choice`, `list`, `object`); `verified_at` records
the user's review. No fact is hidden from the worker, its terminal, or its model
calls. Facts that
change with time — completed co-op rotations, graduation term, annual minimum
compensation — are derived by the worker and never stored; `coop.anchor` and
`compensation.minimum` are their stored inputs. The worker picks a form-specific
rendering rather than copying facts per application. The former `jobs_profile`
seeded `jobs_facts`; it is now `jobs_archive_profile`, read-only, and is dropped
once every fact is verified.

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
