# How the database works

Hosted Supabase PostgreSQL is the only product database. Supabase owns `auth` and
`storage`; Personal OS owns one private `app` schema. The schema is absent from the
Data API schemas and grants no table access to `anon` or `authenticated`.

FastAPI connects with the managed database credential, opens short transactions,
and assumes `personal_os_api`. Every user-owned row references `auth.users`; the
API derives the UUID from a verified JWT and never accepts it in a body.

The Jobs tables hold structured profile facts, source listings, applications,
immutable versioned documents, reusable answers, tailoring audits, and worker task
leases. `jobs_worker_credentials` stores only SHA-256 token hashes plus scope,
expiry, use, and revocation timestamps. Listing source metadata keeps discovery and
direct application URLs separate and supports non-exclusive role-family arrays.

The private `jobs-documents` Storage bucket accepts PDFs up to 5 MiB. Only FastAPI
uses its backend secret key; the browser and worker never receive it. Approved
document content cannot be mutated or deleted, though current/archive lifecycle
fields can change.

`migrations/` is the only runnable schema history. There is no local Supabase
product runtime or seed workflow.
