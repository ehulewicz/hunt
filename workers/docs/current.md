# Worker current state

Last verified: 2026-08-02

## Milestones

- [x] Scaffold the Python 3.13 package and no-op CLI.
- [x] Keep the active worker free of background processes, browser behavior,
  model calls, and queues until Jobs CRUD exists.
- [ ] After Jobs CRUD exists, implement a foreground PowerShell-launched worker
  that uses subscription-authenticated `claude -p`.
- [ ] Add visible Python Playwright with a dedicated Windows Chrome profile.
- [ ] Enforce the invariant that autofill never submits an application.
