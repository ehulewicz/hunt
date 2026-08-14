# Frontend current state

Last verified: 2026-08-13

## Milestones

- [x] Preserve the Vercel-hosted workspace shell and Google-only PKCE login.
- [x] Replace all six Jobs placeholders with real API-backed flows.
- [x] Add a structured Jobs profile editor with explicit optional EEO fields and
  worker pairing/revocation.
- [x] Show only internships/co-ops, always newest-first, with All/FAANG+/Quant
  and multi-select normalized-location filters.
- [x] Remove search, role-family, career-stage, freshness, and browser source-sync
  controls while retaining batch preparation and direct company application links.
- [x] Make titles the direct ATS link and compress listing/application rows while
  retaining explicit company and normalized location.
- [x] Add confirmation-gated recoverable application deletion, task states,
  actionable errors, missing questions, and explicit autofill retry.
- [x] Add authenticated PDF/patch review and approval that queues autofill.
- [x] Group application facts under Profile and reserve Answers for keyless
  create/update-by-ID long-form responses.
- [x] Keep submitted status manual; neither frontend nor worker infers or records
  a submission without the user doing it.
- [x] Pass lint, types, all 11 unit tests, UI policy, production build, and all 3
  Playwright visual tests.
- [x] Deploy the Jobs frontend to Vercel from commit `c2b90a7`.
- [x] Deploy the simplified listing page to Vercel from commit `1d97fba`.
- [ ] Run authenticated hosted Jobs acceptance.
- [x] Add automatic and manual-retry autofill controls while keeping submitted
  state manual.
- [ ] Deploy the complete Jobs frontend and run authenticated hosted acceptance.
- [ ] Add zero2sudo source controls only after SpeedyApply acceptance passes.
- [ ] Add Calendar and unified Tasks only after Jobs works end to end.
