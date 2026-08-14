# Backend current state

Last verified: 2026-08-13

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
- [x] Validate locally with Ruff, mypy, and 26 passing tests.
- [x] Validate the live strict-US source at exactly 440 rows, including 136
  FAANG+, 51 Quant, and 89 normalized location choices.
- [x] Validate the live four-file source at 1,087 unique internships/co-ops,
  including 247 FAANG+, 104 Quant, and 252 normalized location choices.
- [x] Deploy the Jobs API to Cloud Run revision `personal-os-api-00010-6nm` and
  verify hosted health plus unauthenticated rejection.
- [x] Deploy scheduled ingestion and the simplified listing API to Cloud Run
  revision `personal-os-api-00011-lc5`; verify the first hosted sync writes 1,087
  open listings in 43 seconds.
- [ ] Run authenticated hosted Jobs acceptance.
- [x] Deploy migration `20260814003115_complete_jobs_workflow.sql` and Cloud Run
  revision `personal-os-api-00012-dw2`; verify sync run `31759248462` writes
  exactly 440 open strict-US rows.
- [ ] Deploy the PhD exclusion and record the new strict-US hosted row count.
- [ ] Add zero2sudo ingestion only after SpeedyApply acceptance passes.
- [ ] Add Google integration OAuth only with the Calendar milestone.
