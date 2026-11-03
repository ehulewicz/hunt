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

Facts, the career record, the devices that run the Hunt agent, source listings,
recoverably archived applications with
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
was written for; runs never write it — the rows are old reference the
worker's fallback drafter reads. Answer history and fill events carry
`question_id`, and fill events the `pattern_id` that produced their value.
`jobs_questions.embedding` caches `extensions.vector(1536)` for the single
pinned question-embedding model. It remains in the private `app` schema, and
only the API role reads or writes it.

The archive is the old system, renamed and frozen — the API role can only read
it: `jobs_archive_answers` (the former answer library, 198 rows),
`jobs_archive_profile` (the former profile), and `jobs_archive_documents`. It
serves only the facts session and is dropped after it; no application reads it.
Failure issues double as the repair queue: `stopped_at` (`format` or `strategy`)
says whether Repair can retry one, `candidate` holds the rule a repair found, and
`repaired_at` and `validated_at` date the two steps; a `repaired` or `validated`
issue must carry its candidate. An issue the fixer works on is `fixing`, then
`fixed` (with its commit) or `unsolved`. Failure issues the old dispatch left
open are `archived`; a signature that fails again reopens. `jobs_changes` holds
every change to a pattern, fact, prose, question disposition, validated strategy,
or code, with who made it and the failure issue it fixed (`failure_issue_id`).
`jobs_autofill_runs.model_calls` counts a run's model calls; `model_usage` (jsonb) carries the per-lane call and token totals the latest step reported (pattern, audit, draft, code — pattern and code are learning).

An observed history row is identified by run and event index; final rows retain the
submitted or drafted value with step and control context. Autofill activates only a
pattern it learned from a model verdict and checked (origin `model`), and never
writes prose. `jobs_worker_credentials` stores only SHA-256 token hashes plus
scope, expiry, use, and revocation timestamps. Listing source metadata keeps
discovery and direct application URLs separate, records the FAANG+/Quant/general
source section, and stores normalized location arrays for match-any filtering.
Composite, partial, and GIN indexes support newest-first discovery, unanswered
questions, and location overlap. The worker task queue and tailoring audit tables
were dropped when the task worker was retired.

`jobs_controls`, `jobs_strategies`, and `jobs_fill_events` measure the fill
path. A control is a kind (the seven seeded ones, or a name the fixer's code
adds) plus a `jsonb` structural attribute
vector, unique per user, and never includes the ATS or the question. A strategy
is a named way of operating a kind, with a precedence and a lifecycle status; the
ten that exist in code are seeded `active`. A fill event is one attempt: the
question's normalized key, the resolver id, the control, the strategy, the ATS
as a dimension only, a value hash, `stopped_at` (`pattern`, `format`,
`strategy`, or null) with `operated_ok` (true read back, false failed, null not
attempted), and `options`, the JSON array of what the control offered (null
when it offered nothing or its menu was never read), so a learned change can be
replayed against what the page showed. Each attempt also stores `knowledge`,
a JSON snapshot of the fact values, types, and seed the worker used, so replay
can reproduce old answers without borrowing today's posting or facts. Legacy
events leave it null. `question_key` and `resolver_id` stand in until `jobs_questions` and
`jobs_patterns` exist and add real foreign keys.

`jobs_devices` is one row per machine that runs the Hunt agent, keyed by an id
the device generates once and keeps in `~/.config/personal-os/device.json`. It
holds the Windows machine name, the `https://….ts.net` address `tailscale serve`
publishes it at, the commit it runs and whether it has local changes, whether `os
doctor` passes there (`problem` is the first failing check, present exactly when
not `ready`), the run in progress (`active_run_id`, cleared if the run is
deleted), and `last_seen_at`, which the API sets from its own clock on each
30-second heartbeat. The page treats a device seen in the last 90 seconds as
online. The API role can insert, update, and read rows, not delete them.

`jobs_education`, `jobs_resume_entries`, `jobs_resume_bullets`, and `jobs_skills`
are the career record, the canonical resume. Dates are month-precision `date`s
(the first of the month); an entry's `end_date` is null with a start date for
"Present", and a single-month project stores only `end_date`. Sections are
`experience`, `research`, `project`, and `activity`; only experience carries an
`employment_type`, so the rest can never count toward tenure. Entries, bullets,
and schools each carry `on_resume` — the record keeps what the printed resume
omits — and education rows carry a `level` (`high_school`, `undergraduate`,
`graduate`) and `coursework` as `text[]`. Entry rows are stable across saves so
`jobs_library_entries.entry_id` can reference them with `on delete set null`.
An entry's place is `city`, `region` (a state or province as written),
and `country` (a name); `location` is their rendering, written only for an API
that still reads it, and is dropped once none does. `job_title` and `employer`,
when set, are what a work-history form gets instead of `title` and `org`. One
education row per user is `is_primary`, and a bullet can never hold a
semicolon. `jobs_archive_documents` is the former `jobs_documents` — a 2026-08
copy of the resume and two tailored resumes — renamed and frozen: the API role
can only read it.

`jobs_library_entries` is the narrative library: one row per code, a `section`
(`interest`, `direction`, `evidence`, `story`, `entry`, `note`), a prompt with
optional guidance and answer, an optional `entry_id` into the career record,
a `human_percent` share (0 to 100) a GPTZero scan sets — cleared when the
answer changes — and a display order. `jobs_prose_gaps` records a long-form question the library covers only
partly or not at all (`coverage` `partial` or `missing`, a fixed
`family`, an optional application); a partial unique index makes at most one
gap per question open at a time, and `resolved_at` closes it — the API role
updates, never deletes. `jobs_changes` accepts `library` as a subject for the
library's write history.

`jobs_facts` holds one row per fact, keyed per user by a dotted name, with a
`jsonb` value whose shape a check constraint ties to `value_type` (`text`,
`boolean`, `number`, `date`, `choice`, `list`, `object`); `verified_at` records
the user's review. No fact is hidden from the worker, its terminal, or its model
calls. Facts that
change with time — completed co-op rotations, graduation term, annual minimum
compensation — are derived by the worker and never stored; the career record
and `compensation.minimum` are their inputs. The worker picks a form-specific
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
