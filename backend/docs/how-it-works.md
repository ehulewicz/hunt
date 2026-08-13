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

`/v1/jobs/*` owns profile editing, listing filtering, application preparation,
document review, answer-library management, worker pairing, and task leasing.
The scheduled SpeedyApply importer reads the US and international internship files
from both the 2027 SWE and AI/ML repositories. It runs at 13:00 UTC, one hour after
the upstream repositories, fetches the four files concurrently, and batches its
database upserts. The browser cannot trigger a source sync.
An empty parse fails before database mutation so an upstream format change cannot
silently close the entire listing set.

Listings retain their FAANG+, Quant, or general source section and normalized
location facets. The API always limits discovery to internships/co-ops and orders
them newest-first. Selected locations use match-any semantics. The direct
company/ATS URL is used for application work while the GitHub file remains the
discovery URL.

Browser sessions create revocable 30-day worker credentials. A paired worker may
claim only that user's Jobs tasks, heartbeat its lease, fetch the approved bundle,
upload a PDF, and report a result. Worker results create draft documents and
tailoring audit rows. Approval and submitted status remain user actions.
