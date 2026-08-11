# How the backend works

Cloud Run validates browser bearer tokens against hosted Supabase JWKS, then
enforces the configured single UUID and verified email. Invalid authentication is
`401`; a valid but unapproved identity is `403`. No request accepts a user ID.

FastAPI is the only application database and Storage client. Each PostgreSQL
transaction assumes the non-login `personal_os_api` role before accessing the
private `app` schema. Private PDF objects are read and written server-side with a
Supabase secret key from GCP Secret Manager.

`POST /v1/bootstrap` seeds the known Jobs profile, approved canonical `main`
resume, and reusable answer facts idempotently. Unknown or optional EEO values are
not inferred.

## Jobs boundary

`/v1/jobs/*` owns profile editing, listing sync/filtering, application preparation,
document review, answer-library management, worker pairing, and task leasing.
SpeedyApply sync reads all four 2027 feeds and writes every parsed row. Role families
are non-exclusive tags, not an ingest gate; an unmatched title is stored as
`other`. The direct company/ATS URL is used for application work while the GitHub
file remains the discovery URL.

Browser sessions create revocable 30-day worker credentials. A paired worker may
claim only that user's Jobs tasks, heartbeat its lease, fetch the approved bundle,
upload a PDF, and report a result. Worker results create draft documents and
tailoring audit rows. Approval and submitted status remain user actions.
