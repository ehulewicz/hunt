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
date and no end. An entry's location is stored as city, region, and country, and
rendered "Atlanta, GA" at home and "Metz, France" abroad; a document that gives
only `location` is parsed into them, and one whose `location` disagrees with its
parts is refused. `jobTitle` and `employer` are what a work-history form gets
when the heading is not one (a research heading names the lab). The document's contact block is not stored here — it is rendered
from the `identity`, `contact`, and `work_auth.us_citizen` facts, and a write
whose header disagrees is refused with the fact to set instead. Writes refuse a
semicolon or keyword emphasis in a bullet and an em dash in a heading.

Application facts live in `jobs_facts`, one row per fact keyed by a dotted name
(`education.graduation_date`), with a JSON value typed by `value_type`.
`/v1/jobs/facts` lists, sets, verifies, and deletes them; a write is validated
against its type here and by a database check. Setting a value marks it verified.
Saving an unchanged value, label, type, and verification state is a no-op;
an explicit verification still records the new verification time. Fact keys
are limited to 200 characters, matching the change history.
An empty text or list is a fact too: the answer is none, and the worker leaves
that field blank on purpose. A choice is never empty.
Unknown or optional EEO values are not inferred.

### Jobs endpoints

`/v1/jobs/*` owns facts, the career record, listing filtering, the application
queue and lifecycle, questions, patterns, prose, the archive, the Inbox,
synchronous autofill runs, and failure history.

### Questions, patterns, prose, and the archive

`jobs_answers.py` holds what the system learns (design §0). A question is
recorded the first time a fill event or an answer names it, unless its label
asks nothing: a file upload, an unlabeled control's fingerprint, a lone `Yes`
or `No`, a viewer's or site's own tools, or a section heading. A pattern binds a
question — or, as an expression, any phrasing — to a fact; only `active`
patterns reach a run. A binding names its question by id, or by label, which
records the question if it is new: that is how the worker saves what a model
verdict taught, active with origin `model`, once it has checked it (R67). Such a
pattern is refused (409) when it would undo a decision of yours: one you
rejected or retired, a binding over the question's existing binding, a question
you marked one-off or long-form, or an expression nesting one repetition in
another; nor is an expression saved again while it waits in the Inbox. A fill
event never proposes a pattern. `GET /v1/jobs/inbox` lists candidate and
repaired patterns with the answers recorded for their question (`checks` holds
those whose replay failed, apart from plain `candidates`), unbound short
questions you answered, unapproved prose, `facts` waiting on you (never
verified, named missing by a run's `fact_gap` issue, or contradicted by the
replay of their latest change), and `unsolved` failure issues the fixer gave up
on. An unbound question that active expressions already decide
(they match it and name one fact, as the worker's resolver requires) is left
out, and so is one recorded before the rule above that asks nothing. Accepting a binding (`PATCH /patterns/{id}` to `active`)
retires the question's previous binding and marks the question `pattern`, and
rejecting or retiring one hands the question back. A long-form answer is saved
as unapproved prose as soon as a step carries it, and again at the run's end,
one row per application and question holding the latest text, with `submitted` and its `provenance`: the spans the worker found
drafted by a model or taken from a previous answer; text in neither is yours.
Approved prose reused as it was is not re-saved. The old
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
`repaired` → `validated` once you watched it, or back to `open`. The fixer
moves an issue to `fixing` while it works on it, to `fixed` (which requires the
commit, and dates the live readback) once a code fix is kept, or to `unsolved`
when its attempts run out; an unsolved issue can be taken again. A fixed,
validated, repaired, archived, or unsolved signature that fails again reopens;
one being fixed stays with the fixer. Each step may carry the run's
`model_calls` so far, and a run's count only grows. `GET /autofill/sites`
folds runs by site, the last two labels of the final URL's host (else the
application URL's), newest first: each site's run count and its latest run, with
that run's status, model calls, and how many failure issues it hit are still
`open` or `fixing`, and `unsolved`. `GET
/autofill/failures/{id}/target` names an unsubmitted application that showed the
question, another posting before the one that failed (R26), and every run's
context carries the validated strategy candidates as control preferences. FastAPI alone writes sanitized artifacts to the private autofill bucket
and verifies ownership on upload and download; storage paths are never trusted as
authorization. Submission and submitted status stay manual.

### Changes and sightings

`jobs_changes.py` keeps the history of what Jobs knows (R69). Every pattern,
fact, prose, and question-disposition write records a `jobs_changes` row in its
own transaction, and validating a strategy records one too: the subject
(`pattern`, `prose`, or `question` by id, `fact` by key, `strategy` by its
issue's signature, `code` by commit), the action, the row before and after as
JSON, who made it (`you`, `inbox`, `model`, `repair`, or `worker`), and, when
the write names one, the failure it answered (a run of yours, the question's
key, and the failure issue). A fact write may carry the worker's replay of the
new value; your fact is saved either way, and a failed replay puts it in the
Inbox as contradicted. A fact created or updated without one (the web Library,
`os hunt facts set`) is recorded `pending`; the next `os hunt apply` or
`os hunt replay` replays it and resolves it. A binding that
replaces another records the one it retired too, as does marking its question
one-off, long-form, or unbound. A pattern the worker learned
arrives with its verification, the sightings it replayed and how many it made
wrong; one that made any wrong cannot arrive active, so it waits in the Inbox,
and `GET /v1/jobs/inbox` carries that failed change as the candidate's `check`.
Any other change you make, marking a fact verified or deleting one included, is
recorded unverified. `POST /v1/jobs/changes` records a code
change: its commit as the subject, origin `repair` by default (`worker` for the
fixer), the commit it followed as `before`, its detail (diff, files, attempts,
tests, replay, live readback) in `after`, and its verification, or `pending`
without one. `PATCH /v1/jobs/changes/{id}/verification` records the replay a
pending change waited for, once; a verified change is never rewritten (404).
`GET /v1/jobs/changes` filters by subject, subject id, and status, newest
first.

`GET /v1/jobs/sightings` serves what a change is replayed against: each short
answer submitted with an application, newest first, with the input type or tag
it was typed into and the last fill event its run made for that question (the
control's kind and attributes, the options it offered when they were kept, the
fact and formatter, the hash of what was filled, and the exact fact values,
types, and seed used for that attempt). The submitted answer is
the one you accepted. An answer from before `jobs_questions` has no question
row, so its own normalized key stands in for one. Historical attempts without
a knowledge snapshot remain readable; the worker identifies replay cases
whose original inputs cannot be verified.

### Similar questions

`POST /v1/jobs/questions/similar` compares question labels using cosine
similarity. `jobs_search.py` calls OpenAI `text-embedding-3-small` in batches,
caches each recorded question's 1536-dimensional vector, and returns matches
above the requested threshold (default 0.65), with only approved prose attached.
`prose_only` restricts matches to questions that have approved writing. The same
endpoint can find short or one-off questions without that restriction. Only
question labels reach OpenAI. Provider calls run outside database transactions;
missing or invalid provider results return 503 without saving vectors. Exact
comparisons scan this single user's small corpus; no approximate index is needed.
The model is pinned in code; replacing it requires clearing and rebuilding all
cached vectors. The key is `PERSONAL_OS_API_OPENAI_API_KEY`, supplied from GCP
Secret Manager; it is never sent to the worker or frontend.

`POST /v1/jobs/questions/rank-facts` orders facts for each question by the
cosine similarity of their labels, closest first, up to `limit` keys. The worker
sends fact keys and labels, never values; label vectors are cached in memory
(bounded) and never stored. The worker shows every fact to a model and only
lists these first, so a poor ranking cannot hide the right one.

### Devices

`/v1/jobs/devices` is the list of machines that run the Hunt agent
(`jobs_devices.py`). A device lists itself: its agent sends `PUT
/v1/jobs/devices/{device_id}` every 30 seconds through its local API, and the
device's own id is the path key while the user is always the verified JWT's. The
heartbeat says the device's name, its tailnet address, the commit it runs and
whether it has local changes, whether `os doctor` passes there (and if not, the
first failing check), and the run it is busy with, which must be one of yours. The
address must be an `https://<name>.ts.net` name, because the page sends your token
to it. `GET` lists your devices with `online` computed from the last heartbeat
(under 90 seconds) and the busy run's company and title. An id another user holds
is a `409`. Nothing here carries the live view; the page connects to the device
directly over the tailnet.

### A posting by its URL

`POST /v1/jobs/listings/from-url` returns the posting whose application or
discovery URL is the one given (exactly, else with the query string dropped
when that names a single posting). A URL no posting has becomes a `manual`
posting once a company and title are given; the worker reads them off the page,
which is how a referral link off the board is applied to.

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

Currently 130 tests. `mypy` is scoped to `src/` because the test suite is not
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
  jobs_changes.py   /v1/jobs/changes and /sightings: the change history and what it is replayed against
  jobs_search.py    cached question embeddings and approved-prose similarity search
  jobs_devices.py   /v1/jobs/devices: the heartbeat upsert and the device list
tests/              pytest suite, async mode auto
Dockerfile          builds from the repository root build context, not from here
```

Deployment and secrets: [`../docs/deployment.md`](../docs/deployment.md).
Schema: [`../database/README.md`](../database/README.md).
