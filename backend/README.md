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

`POST /v1/bootstrap` idempotently seeds the approved canonical `main` resume from
`artifacts/career/main.json`. The Docker image copies that same tracked file from
the repository-root build context, so local resume generation and hosted bootstrap
never maintain duplicate JSON. It still fills defaults into the retired
`jobs_profile` table, which nothing reads; both go when that table is dropped.

Application facts live in `jobs_facts`, one row per fact keyed by a dotted name
(`education.graduation_date`), with a JSON value typed by `value_type`.
`/v1/jobs/facts` lists, sets, verifies, and deletes them; a write is validated
against its type here and by a database check. Setting a value marks it verified.
Unknown or optional EEO values are not inferred.

### Jobs endpoints

`/v1/jobs/*` owns facts, listing filtering, the application queue and
lifecycle, document review, answer-library management, synchronous autofill runs,
and failure history. Discovery is always limited to internships/co-ops, ordered
newest-first by `jobs_postings.created_at` (discovery into this system, not
source-posting age), with match-any location selection and the FAANG+/Quant/general
source section retained. The direct company/ATS URL is used for application work
while the GitHub file remains the discovery URL.

The signed-in CLI creates an autofill run from a posting ID. FastAPI upserts the
application and returns only that user's direct URL, posting context, facts,
approved answer bases, scoped variants, and duplicate-company warnings. Step
results, changed-answer observations, and final values come back through
authenticated endpoints and are stored as application history — only explicit
answer-library commands approve reuse. `GET /v1/jobs/answer-review` is the
read-only review feed over those events (limit 1–200).

`POST /v1/jobs/autofill/runs/{id}/finish` is atomic: run completion, final history,
and application status share one transaction, and it requires a status of `applied`,
`skipped`, or `archived` consistent with the submitted flag. Only `applying` or the
same target status may transition; a concurrent lifecycle change returns `409` and
rolls back. The legacy `/complete` endpoint keeps its history-only contract, and
completed-run retries return `404` so they cannot duplicate final history. Invalid
question/value/provenance metadata fails with `422` before persistence.

Failure signatures deduplicate issues while occurrence rows preserve run/step
evidence. FastAPI alone writes sanitized artifacts to the private autofill bucket
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

Currently 73 tests. `mypy` is scoped to `src/` because the test suite is not
annotated; `pyproject.toml` already pins `packages = ["personal_os_backend"]`, so bare
`uv run mypy` — the form CI uses — checks the same files.

Serve the API locally with `uv run personal-os-backend`. It reads
`src/personal_os_backend/.env`, resolved from the package rather than the working
directory, so it starts the same way from anywhere.

## Structure

```text
src/personal_os_backend/
  main.py           create_app, CORS, error middleware, /health and /v1/account
  auth.py           JWKS fetch, JWT validation, single-identity enforcement
  config.py         pydantic-settings Settings, PERSONAL_OS_API_* env vars
  database.py       psycopg async pool, role assumption, bootstrap store
  models.py         non-Jobs response models
  jobs_router.py    every /v1/jobs/* route
  jobs_models.py    Jobs request/response schemas and validation
  jobs_store.py     all Jobs SQL (the largest module by far)
  jobs_sources.py   SpeedyApply README parsing and location normalization
  jobs_sync.py      standalone sync entry point, runs as the Cloud Run Job
  jobs_storage.py   private Storage bucket reads and writes
  jobs_answers.py   question-key normalization for deterministic matching
tests/              pytest suite, async mode auto
Dockerfile          builds from the repository root build context, not from here
```

Deployment and secrets: [`../docs/deployment.md`](../docs/deployment.md).
Schema: [`../database/README.md`](../database/README.md).
