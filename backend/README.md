# Backend

Python 3.13 FastAPI service. It is the only application client of the private
`app` PostgreSQL schema and of the private Storage buckets, and it owns
authorization and all Jobs business logic.

This one directory produces **two** deployed artifacts from the same image:

| Artifact | What it is | Entrypoint |
|---|---|---|
| `personal-os-api` | Cloud Run **service** — the HTTP API | `uvicorn personal_os_backend.main:app` (`Dockerfile`) |
| `personal-os-jobs-sync` | Cloud Run **job** — the daily importer | `python -m personal_os_backend.jobs_sync` (`deploy.yml`) |

The names differ by viewpoint, not by thing: `backend/` is the source boundary,
`personal-os-api` is what it is called once deployed. The Cloud Run service, its
service account, the GCP secret names and the `PERSONAL_OS_API_` settings prefix
all keep the `api` spelling because they are live infrastructure, not Python
names.

## How it works

Cloud Run validates browser bearer tokens against hosted Supabase JWKS, then
enforces the configured single UUID and verified email. Invalid authentication is
`401`; a valid but unapproved identity is `403`. **No request accepts a user ID** —
it is always derived from the verified JWT.

Each PostgreSQL transaction assumes the non-login `personal_os_api` role before
touching the `app` schema. Private PDF objects are read and written server-side
with a Supabase secret key from GCP Secret Manager.

`POST /v1/bootstrap` confirms the verified identity for the browser after
sign-in and writes nothing; the profile and resume copy it used to seed are
archived.

### Career record

`/v1/jobs/career` holds the resume as rows (`jobs_career.py`): one primary
education row, entries for experience, research, and projects with real
month-precision dates and an employment type, ordered bullets, and skill
categories. `GET` returns the rows and the resume document rendered from them in
the shape `artifacts/career/main.json` has always had; `PUT` replaces the whole
record from such a document in one transaction; `POST /import` does the same only
while the record is empty. Rendering keeps the authored text: "Middleware Engineer
(Co-op)" is a title plus employment type `coop`, "May 2025 – Present" is a start
date and no end. The document's contact block is not stored here — it is rendered
from the `identity`, `contact`, and `work_auth.us_citizen` facts, and a write
whose header disagrees is refused with the fact to set instead. Writes refuse a
semicolon or keyword emphasis in a bullet and an em dash in a heading.

Application facts live in `jobs_facts`, one row per fact keyed by a dotted name
(`education.graduation_date`), with a JSON value typed by `value_type`.
`/v1/jobs/facts` lists, sets, verifies, and deletes them; a write is validated
against its type here and by a database check. Setting a value marks it verified.
Unknown or optional EEO values are not inferred.

### Jobs endpoints

`/v1/jobs/*` owns facts, the career record, listing filtering, the application
queue and lifecycle, questions, patterns, prose, the archive, the Inbox,
synchronous autofill runs, and failure history.

### Questions, patterns, prose, and the archive

`jobs_answers.py` holds what the system learns (design §0). A question is
recorded the first time a fill event or an answer names it. A pattern binds a
question — or, as an expression, any phrasing — to a fact; only `active`
patterns reach a run, and a fill event whose value came from the model and
read back proposes a `candidate` binding in the step's
transaction, never an active one. `GET /v1/jobs/inbox` lists candidates with the
answers recorded for their question, unbound short questions you answered, and
unapproved prose; accepting a binding (`PATCH /patterns/{id}` to `active`)
retires the question's previous binding and marks the question `pattern`, and
rejecting or retiring one hands the question back. A submitted long-form answer
is saved as unapproved prose unless it was approved prose reused. The old
answer library is `jobs_archive_answers`, served read-only at
`/v1/jobs/archive/answers` for the facts session and never in a run's context:
no application reads it. Discovery is always limited to internships/co-ops, ordered
newest-first by `jobs_postings.created_at` (discovery into this system, not
source-posting age), with match-any location selection and the FAANG+/Quant/general
source section retained. The direct company/ATS URL is used for application work
while the GitHub file remains the discovery URL.

The signed-in CLI creates an autofill run from a posting ID. FastAPI upserts the
application and returns only that user's direct URL, posting context, facts,
active patterns, approved prose, validated control preferences, and
duplicate-company warnings.
Step results, changed-answer observations, and final values come back through
authenticated endpoints and are stored as application history linked to their
questions; nothing in a run activates a pattern or approves prose.

`POST /v1/jobs/autofill/runs/{id}/finish` is atomic: run completion, final history,
and application status share one transaction, and it requires a status of `applied`,
`skipped`, or `archived` consistent with the submitted flag. Only `applying` or the
same target status may transition; a concurrent lifecycle change returns `409` and
rolls back. The legacy `/complete` endpoint keeps its history-only contract, and
completed-run retries return `404` so they cannot duplicate final history. Invalid
question/value/provenance metadata fails with `422` before persistence.

Every step can also carry fill events, one per attempt to fill a control,
written in the step's transaction by `jobs_registry.py`. A control is upserted
by its kind and structural attributes — never the ATS or the question — so one
widget accumulates observations across companies; a strategy the seed did not
know is registered at precedence 100. Success rates are queries over
`jobs_fill_events`; nothing keeps a counter.

Failure signatures deduplicate issues while occurrence rows preserve run/step
evidence. The issues are also the repair queue: a failure records where the fill
stopped (`format` or `strategy`), and its occurrence keeps the question, fact,
and formatter. `open` → `repaired` requires the candidate rule that worked;
`repaired` → `validated` once you watched it, or back to `open`; a repaired or
validated signature that fails again reopens. `GET
/autofill/failures/{id}/target` names an unsubmitted application that showed the
question, another posting before the one that failed (R26), and every run's
context carries the validated strategy candidates as control preferences. FastAPI alone writes sanitized artifacts to the private autofill bucket
and verifies ownership on upload and download; storage paths are never trusted as
authorization. Submission and submitted status stay manual.

### Scheduled listing sync

The importer reads only the US internship `README.md` files from the 2027 SWE and
AI/ML SpeedyApply repositories. It accepts visible `City, ST`, US-wide, and US
remote forms; rejects visibly international primary locations and titles containing
`PhD`/`Ph.D.`; fetches both files concurrently; and batches its upserts. An empty
parse fails before any mutation, so an upstream format change cannot silently close
the whole listing set. The browser cannot trigger a sync.

It runs as `python -m personal_os_backend.jobs_sync`, a standalone script with
its own database pool that never goes through the HTTP server. In production it
is the Cloud Run Job `personal-os-jobs-sync` (same image and runtime service
account as the API, region `us-east1`), triggered daily at 9:00 AM
America/New_York by Cloud Scheduler job `personal-os-jobs-sync-daily`, which runs
as `personal-os-scheduler` and holds `run.invoker` on that job only. GitHub
Actions no longer runs this sync; its cron drifted hours late.

`deploy.yml` sets the job's image **and its entrypoint** on every deploy. The
entrypoint previously lived only in the job's stored GCP config, so renaming the
backend package left the two disagreeing and the next scheduled run would have
failed. The schedule itself is still only in Cloud Scheduler, declared nowhere in
this repository.

A manual run:

```bash
gcloud run jobs execute personal-os-jobs-sync --region us-east1
```

## Run and verify

The venv is the single workspace one at the repository root; `uv run` from here
uses it. All three must pass:

```bash
cd backend && uv run ruff check . && uv run mypy src/ && uv run pytest -q
```

Currently 116 tests. `mypy` is scoped to `src/` because the test suite is not
annotated; `pyproject.toml` already pins `packages = ["personal_os_backend"]`, so bare
`uv run mypy` — the form CI uses — checks the same files.

Serve the API locally with `uv run personal-os-backend`. It reads
`backend/.env`, resolved from the package rather than the working directory, so it
starts the same way from anywhere.

## Structure

```text
src/personal_os_backend/
  main.py           create_app, CORS, error middleware, /health and /v1/account
  auth.py           JWKS fetch, JWT validation, single-identity enforcement
  config.py         pydantic-settings Settings, PERSONAL_OS_API_* env vars
  database.py       psycopg async pool, role assumption, bootstrap store
  models.py         non-Jobs response models
  jobs_router.py    every /v1/jobs/* route except facts
  jobs_facts.py     /v1/jobs/facts: models, store, routes, per-type value validation
  jobs_career.py    /v1/jobs/career: the resume as rows, rendered to and parsed from its document
  jobs_registry.py  fill-event models and recording, with control and strategy upserts
  jobs_models.py    Jobs request/response schemas and validation
  jobs_store.py     all Jobs SQL (the largest module by far)
  jobs_sources.py   SpeedyApply README parsing and location normalization
  jobs_sync.py      standalone sync entry point, runs as the Cloud Run Job
  jobs_storage.py   private Storage bucket reads and writes
  jobs_answers.py   questions, patterns, prose, the archive, and the Inbox
tests/              pytest suite, async mode auto
Dockerfile          builds from the repository root build context, not from here
```

Deployment and secrets: [`../docs/deployment.md`](../docs/deployment.md).
Schema: [`../database/README.md`](../database/README.md).
