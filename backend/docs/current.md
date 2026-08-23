# Backend current state

Last verified: 2026-08-23

## Milestones

- [x] Implement health, identity, bootstrap, and strict single-user JWT checks.
- [x] Keep FastAPI as the only application database and Storage client.
- [x] Implement Jobs profile, listings, applications, documents, reusable
  answers, worker credentials, task leasing, and worker-result endpoints.
- [x] Ingest only visibly US internship/co-op rows from the US READMEs in both
  SpeedyApply 2027 SWE and AI/ML repositories.
- [x] Exclude source titles containing `PhD` or `Ph.D.` before ingestion.
- [x] Preserve FAANG+/Quant source categories, normalized location facets, and
  both discovery and direct company/ATS URLs.
- [x] Schedule hosted ingestion for 13:00 UTC, remove request-time source sync,
  fetch source files concurrently, and batch database writes.
- [x] Fail safely before database mutation when the source parser returns no rows.
- [x] Classify preferred role families without excluding unmatched or adjacent
  job titles.
- [x] Seed the canonical approved `main` resume and known profile facts during
  bootstrap while leaving optional EEO answers explicit and unset.
- [x] Implement recoverable application archival/reactivation, task cancellation,
  automatic/manual autofill queueing, task status/errors, and missing questions.
- [x] Reserve Answers for create/update-by-ID approved long-form responses and
  keep deterministic factual values in Profile.
- [x] Add authenticated resume review and worker-only approved PDF download.
- [x] Add authenticated synchronous autofill run, step, completion, failure,
  and private artifact endpoints with JWT-derived ownership.
- [x] Add deterministic normalized answer keys and exact/contextual reuse policy.
- [x] Validate locally with Ruff, mypy, and 41 passing tests.
- [x] Validate the live strict-US source at exactly 440 rows, including 136
  FAANG+, 51 Quant, and 89 normalized location choices.
- [x] Validate the live four-file source at 1,087 unique internships/co-ops,
  including 247 FAANG+, 104 Quant, and 252 normalized location choices.
- [x] Deploy the Jobs API to Cloud Run revision `personal-os-api-00010-6nm` and
  verify hosted health plus unauthenticated rejection.
- [x] Deploy scheduled ingestion and the simplified listing API to Cloud Run
  revision `personal-os-api-00011-lc5`; verify the first hosted sync writes 1,087
  open listings in 43 seconds.
- [x] Wrap `jsonb` parameters in `Jsonb` so worker completions stop returning
  `500`, and cover `complete_task` with a regression test.
- [x] Serve base resume viewing and editing: validated master-resume draft
  creation, worker-only draft fetch, preview queueing, and preview PDF storage,
  with no migration because `task_type` is unconstrained.
- [x] Allow in-place updates to main resume drafts and return CORS-visible
  errors so failures surface as a status instead of `Failed to fetch`.
- [x] Move the canonical resume seed to the portable repository artifact
  `artifacts/resume/main.json`; the backend and worker now consume the same
  source, and the generated `main.pdf` is tracked beside it.
- [ ] Run authenticated hosted Jobs acceptance.
- [x] Apply `20260823090000_autofill_failure_learning.sql` in deploy run
  `32630539002`, deploy Cloud Run revision `personal-os-api-00034-68n`, and
  verify health, unauthenticated rejection, and authenticated answer/failure reads.
- [ ] Run authenticated create/step/artifact/complete acceptance with a
  user-selected live application.
- [x] Fix the create-run data-modifying CTE to return the `ats` column consumed
  by its outer query; verify against hosted Postgres through the restarted local
  API by creating and abandoning a real listing-12 run.
- [x] Deploy migration `20260814003115_complete_jobs_workflow.sql` and Cloud Run
  revision `personal-os-api-00012-dw2`; verify sync run `31759248462` writes
  exactly 440 open strict-US rows.
- [x] Deploy the PhD exclusion in Cloud Run revision `personal-os-api-00013-dg6`;
  sync run `31762555087` records 380 open strict-US rows, 60 fewer than before.
- [ ] Add zero2sudo ingestion only after SpeedyApply acceptance passes.
- [ ] Add Google integration OAuth only with the Calendar milestone.

## Local run, verified 2026-08-19

The Jobs CLI migration runs FastAPI locally against hosted Supabase. Verified on
this machine:

- `backend/.env` holds the `PERSONAL_OS_API_` settings. Non-secret values mirror
  the Cloud Run service; `DATABASE_URL`, `WORKER_JWT_SECRET`, and
  `SUPABASE_SECRET_KEY` are read from GCP Secret Manager project
  `example-gcp-project`. The file is mode 600 and matches `.gitignore:36`, so it
  is not tracked.
- `CORS_ALLOWED_ORIGINS` is `[]` locally. The CLI is not a browser and sends no
  Origin, and the Vercel origin is being retired.
- `uv run uvicorn personal_os.main:app --host 127.0.0.1 --port 8000` serves
  `/health` as `200`. `/health` calls `database_is_ready` and returns `503` when
  the pool is down, so a `200` proves the local process reaches hosted Postgres.
- Unauthenticated `/v1/jobs/listings` returns `401`, so authorization behaves the
  same locally as hosted.
- Cloud Run revision `personal-os-api-00017-qkg` is untouched and still serving.

Supabase auth configuration, read through the Management API using
`personal-os-supabase-access-token`:

- Google is enabled, email/password is disabled, refresh token rotation is on,
  and `jwt_exp` is 3600. The one-hour access token is why the CLI must store and
  rotate a refresh token rather than a single pasted token.
- `uri_allow_list` now contains the Vercel callback plus
  `http://127.0.0.1:54321/callback` and `http://localhost:54321/callback`. Both
  loopback entries use `http` deliberately: no certificate authority issues
  certificates for `127.0.0.1`, and RFC 8252 requires plain `http` there.
- End to end on 2026-08-19: the CLI signed in through Google, the local API
  verified the resulting `ES256` Supabase JWT against JWKS, the allowed-account
  check passed, and `GET /v1/jobs/listings` returned 392 open listings from
  hosted Postgres. Nothing about database access changed.


## Worker authentication collapsed, 2026-08-19

The CLI, the runner, and the browser are one process tree on one machine
authenticated as the account owner, so the second credential system had nothing
left to separate.

- `worker_auth.py` is deleted. Every `/v1/jobs/worker/*` endpoint now takes
  `CurrentPrincipal`, so it verifies a Supabase Google JWT against JWKS and
  applies the same allowed-account check as the rest of the API.
- The worker-credential endpoints, models, and store methods are removed, along
  with the now-unused `worker_jwt_secret` and `worker_token_lifetime_days`
  settings and the heartbeat-adjacent credential validation.
- `app.jobs_worker_credentials` is now unused. Dropping it is a separate
  migration, not yet applied, and is tracked with the leasing removal.
- Backend Ruff, mypy, and all 35 tests pass. Verified live: `hunt doctor` reports
  `Local API accepted the session`, which exercises `/v1/jobs/worker/doctor`.

## Portable resume artifact, 2026-08-22

- `artifacts/resume/main.json` is the only canonical resume JSON in the
  repository. Backend bootstrap reads it directly and the root-context Docker
  build copies it into the image.
- `artifacts/resume/main.pdf` is the deterministic, one-page Letter rendering
  tracked in Git. The temporary LaTeX file is ignored.
- Backend Ruff, mypy, and all 38 tests pass. A local Docker-image build could not
  run because Docker Desktop integration is unavailable in this WSL instance;
  the deployment workflow uses the corrected repository-root build context.
