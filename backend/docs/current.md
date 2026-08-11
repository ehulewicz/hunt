# Backend current state

Last verified: 2026-08-10

## Milestones

- [x] Implement health, identity, bootstrap, and strict single-user JWT checks.
- [x] Keep FastAPI as the only application database and Storage client.
- [x] Implement Jobs profile, listings, applications, documents, reusable
  answers, worker credentials, task leasing, and worker-result endpoints.
- [x] Ingest every listing from the four SpeedyApply 2027 feeds and preserve both
  discovery and direct company/ATS URLs.
- [x] Classify preferred role families without excluding unmatched or adjacent
  job titles.
- [x] Seed the canonical approved `main` resume and known profile facts during
  bootstrap while leaving optional EEO answers explicit and unset.
- [x] Validate locally with Ruff, mypy, and 9 passing tests.
- [x] Deploy the Jobs API to Cloud Run revision `personal-os-api-00010-6nm` and
  verify hosted health plus unauthenticated rejection.
- [ ] Run authenticated hosted Jobs acceptance.
- [ ] Add an explicit approved-document autofill queue after preparation and
  approval are accepted end to end.
- [ ] Add zero2sudo ingestion only after SpeedyApply acceptance passes.
- [ ] Add Google integration OAuth only with the Calendar milestone.
