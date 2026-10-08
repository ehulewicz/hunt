# Docs

> A thing is documented in exactly one place. A directory's README explains that
> directory. `docs/` holds only what spans directories. Neither repeats the other.

So: how the backend works is in `backend/README.md`, not here. What lives here is
cross-boundary, reference, or historical.

## Contents

| Path | What it is |
|---|---|
| [`deployment.md`](deployment.md) | How production is built and deployed: Vercel, the GitHub Actions `Deploy` workflow, GCP Secret Manager, test ports, and the one-time Google OAuth bootstrap. |
| [`jobs-cli.md`](jobs-cli.md) | The `os hunt` command surface and the behavior of headed apply. The user-facing reference for the Jobs CLI. |
| [`superpowers/specs/`](superpowers/specs/) | Design specs for work not yet built. A spec is a plan, not a description of the current system. |
| [`third-party/`](third-party/) | Reviews of external projects and extensions, recording what was reimplemented and what was deliberately not vendored. |
| [`archived/`](archived/) | Superseded documentation. Nothing there describes the current system. |

## Where the rest went

| Looking for | Read |
|---|---|
| Overall shape, architecture decisions, verification gates | [`../README.md`](../README.md) |
| Rules for agents working in this repository | [`../AGENTS.md`](../AGENTS.md) |
| API, authorization, Jobs endpoints, scheduled listing sync | [`../backend/README.md`](../backend/README.md) |
| Schema, migrations, Storage buckets | [`../database/README.md`](../database/README.md) |
| UI, browser auth, frontend rules | [`../frontend/README.md`](../frontend/README.md) |
| The `os` CLI and browser automation internals | [`../workers/README.md`](../workers/README.md) |
| Resume, cover letter, and other tracked artifacts | [`../artifacts/README.md`](../artifacts/README.md) |
