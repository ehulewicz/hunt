# How the database works

Hosted Supabase PostgreSQL is the only product database. Supabase owns `auth` and
`storage`; Personal OS owns one private `app` schema. The schema is absent from the
Data API schemas and grants no table access to `anon` or `authenticated`.

FastAPI connects with the managed database credential, opens short transactions,
and assumes `personal_os_api`. Every user-owned row references `auth.users`; the
API derives the UUID from a verified JWT and never accepts it in a body.

The Jobs tables hold structured profile facts, source listings, recoverably
archived applications with a lifecycle status (`queued`, `applied`, `oa`,
`oa_completed`, `interview`, `offer`, `rejected`, `archived`), immutable
versioned documents, long-form answers, synchronous autofill runs, deduplicated
failure issues, and failure occurrences. The worker task queue and tailoring
audit tables were dropped when the task worker was retired. `jobs_worker_credentials` stores
only SHA-256 token hashes plus scope,
expiry, use, and revocation timestamps. Listing source metadata keeps discovery and
direct application URLs separate, records FAANG+/Quant/general source sections,
and stores normalized location arrays for match-any filtering. Composite, partial,
and GIN indexes support newest-first discovery, unanswered
questions, and location overlap.

The profile stores graduation date separately from the explicit academic term,
a decimal completed-co-op-term count, and salary amount separately from its
`hour|year` period. Current bootstrap facts are December 15, 2027 / Fall, 4.5
completed terms, and USD 35/hour; the worker chooses a form-specific rendering
without copying those facts per application.

The private `jobs-documents` Storage bucket accepts PDFs up to 5 MiB. The private
`jobs-autofill-artifacts` bucket accepts PNG, HTML, text, and JSON evidence up to
10 MiB. Only FastAPI
uses its backend secret key; the browser and worker never receive it. Approved
document content cannot be mutated or deleted, though current/archive lifecycle
fields can change.

`migrations/` is the only runnable schema history. There is no local Supabase
product runtime or seed workflow.
