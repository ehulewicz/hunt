# FastAPI Foundation Implementation Plan

> Execute sequentially on `main`. The user explicitly prohibited branches,
> worktrees, background agents, and delegated implementation.

**Goal:** Build the first runnable Personal OS API foundation while teaching
the purpose of each dependency, boundary, and request step.

**Architecture:** Supabase Auth proves identity. FastAPI validates the token,
opens a restricted request transaction, and passes trusted work to
`modules/core`. PostgreSQL maps the Auth UUID to its internal owner and forced
RLS remains the final tenant boundary.

**Tech stack:** Python 3.13, uv, FastAPI, Uvicorn, Pydantic Settings, Psycopg 3,
PyJWT, HTTPX, structlog, pytest, Ruff, and mypy.

## Global constraints

- Work directly on `/home/eahtr/personal-os` on `main` and push the verified commit.
- Do not accept a caller-provided owner ID as authorization input.
- Do not add frontend, OAuth UI, hosted infrastructure, new schema entities,
  Storage behavior, an ORM, or broad domain routes.
- Preserve unrelated archived and Northhealth generated-file changes.
- Update living documentation with behavior changes.

## Tasks

- [x] Repair the committed root package-manifest conflict and retain Supabase CLI 2.109.1.
- [x] Create standalone uv packages for `services/api` and `modules/core`.
- [x] Add typed settings, structured logs, request IDs, pool lifecycle, and health routes.
- [x] Add explicit JWKS and Auth-server JWT validation modes.
- [x] Add transaction-local authenticated and public-reader role dependencies.
- [x] Add framework-independent core bootstrap/context/demo operations.
- [x] Add `GET /livez`, `GET /readyz`, `POST /v1/core/bootstrap`, and `GET /v1/demo`.
- [x] Add unit, API, and real local-Supabase integration tests.
- [x] Complete full verification and review the diff; commit and push `main`.

## Acceptance

- Liveness works without PostgreSQL; readiness hides failure details.
- Invalid bearer tokens produce the same 401 response and inactive accounts produce 403.
- Bootstrap is idempotent and never accepts or returns an owner ID.
- Demo requests use only `personal_os_public_reader` and forced RLS.
- A one-connection integration test alternates two human tenants and the demo path.
- Unit/API/integration tests, Ruff, mypy, pgTAP, and the 59-table contract all pass.
