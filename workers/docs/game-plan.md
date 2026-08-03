# Worker game plan

## Status — 2026-08-02

- Python 3.13 package and no-op CLI scaffolded.
- No background process, browser, model call, or queue behavior exists.

## Next

After Jobs CRUD exists, implement a foreground PowerShell-launched worker that uses
subscription-authenticated `claude -p`, then visible Python Playwright with a
dedicated Windows Chrome profile. Autofill must never submit an application.
