# Backend current state

Last verified: 2026-08-13

## Milestones

- [x] Implement health, identity, bootstrap, and strict single-user JWT checks.
- [x] Keep FastAPI as the only application database and Storage client.
- [x] Implement Jobs profile, listings, applications, documents, reusable
  answers, worker credentials, task leasing, and worker-result endpoints.
- [x] Ingest every internship/co-op listing from the US and international files
  in both SpeedyApply 2027 SWE and AI/ML repositories.
- [x] Preserve FAANG+/Quant source categories, normalized location facets, and
  both discovery and direct company/ATS URLs.
- [x] Schedule hosted ingestion for 13:00 UTC, remove request-time source sync,
  fetch source files concurrently, and batch database writes.
- [x] Fail safely before database mutation when the source parser returns no rows.
- [x] Classify preferred role families without excluding unmatched or adjacent
  job titles.
- [x] Seed the canonical approved `main` resume and known profile facts during
  bootstrap while leaving optional EEO answers explicit and unset.
- [x] Validate locally with Ruff, mypy, and 15 passing tests.
- [x] Validate the live four-file source at 1,087 unique internships/co-ops,
  including 247 FAANG+, 104 Quant, and 252 normalized location choices.
- [x] Deploy the Jobs API to Cloud Run revision `personal-os-api-00010-6nm` and
  verify hosted health plus unauthenticated rejection.
- [ ] Run authenticated hosted Jobs acceptance.
- [ ] Add an explicit approved-document autofill queue after preparation and
  approval are accepted end to end.
- [ ] Add zero2sudo ingestion only after SpeedyApply acceptance passes.
- [ ] Add Google integration OAuth only with the Calendar milestone.
