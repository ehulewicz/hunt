# Frontend current state

Last verified: 2026-08-13

## Milestones

- [x] Preserve the Vercel-hosted workspace shell and Google-only PKCE login.
- [x] Replace all six Jobs placeholders with real API-backed flows.
- [x] Add a structured Jobs profile editor with explicit optional EEO fields and
  worker pairing/revocation.
- [x] Show only internships/co-ops, always newest-first, with All/FAANG+/Quant
  and multi-select normalized-location filters.
- [x] Start listings directly with one compact category/location/prepare control
  bar and remove page copy, visible category labeling, counts, and results header.
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
- [x] Pass lint, types, all 13 unit tests, UI policy, and production build.
- [ ] Re-run all 3 Playwright visual tests, including the new 800 px toolbar
  overflow and Axe checks; local elevation is blocked by the environment usage
  limit.
- [x] Deploy the Jobs frontend to Vercel from commit `c2b90a7`.
- [x] Deploy the simplified listing page to Vercel from commit `1d97fba`.
- [ ] Run authenticated hosted Jobs acceptance.
- [x] Add automatic and manual-retry autofill controls while keeping submitted
  state manual.
- [x] Deploy the complete Jobs frontend from commit `204ab00`; GitHub/Vercel
  report production success and `/jobs/listings` returns `200`.
- [ ] Run authenticated hosted acceptance after creating a Local runner
  credential in Profile.
- [ ] Deploy the simplified listing controls.
- [ ] Add zero2sudo source controls only after SpeedyApply acceptance passes.
- [ ] Add Calendar and unified Tasks only after Jobs works end to end.
