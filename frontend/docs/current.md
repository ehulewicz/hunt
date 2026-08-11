# Frontend current state

Last verified: 2026-08-10

## Milestones

- [x] Preserve the Vercel-hosted workspace shell and Google-only PKCE login.
- [x] Replace all six Jobs placeholders with real API-backed flows.
- [x] Add a structured Jobs profile editor with explicit optional EEO fields and
  worker pairing/revocation.
- [x] Add inclusive listings with default internship/daily filters, optional
  SWE/data engineering/ML engineering/data science/other tags, search, source
  sync, batch preparation, and direct company application links.
- [x] Add application tracking, immutable document review/approval/download,
  and an approved reusable answer library.
- [x] Keep submitted status manual; neither frontend nor worker infers or records
  a submission without the user doing it.
- [x] Pass lint, types, unit tests, UI policy, production build, and all 3
  Playwright visual tests.
- [x] Deploy the Jobs frontend to Vercel from commit `c2b90a7`.
- [ ] Run authenticated hosted Jobs acceptance.
- [ ] Add an autofill action only after an application has an approved PDF and
  approved answers; the worker must still stop before Submit.
- [ ] Add zero2sudo source controls only after SpeedyApply acceptance passes.
- [ ] Add Calendar and unified Tasks only after Jobs works end to end.
