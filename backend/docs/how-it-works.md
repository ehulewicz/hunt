# How the backend works

Cloud Run validates browser bearer tokens against hosted Supabase JWKS, then
enforces the configured single UUID and verified email. Invalid authentication is
`401`; a valid but unapproved identity is `403`. No request accepts a user ID.

FastAPI is the only application database and Storage client. Each PostgreSQL
transaction assumes the non-login `personal_os_api` role before accessing the
private `app` schema. Private PDF objects are read and written server-side with a
Supabase secret key from GCP Secret Manager.

`POST /v1/bootstrap` seeds the known Jobs profile and approved canonical `main`
resume idempotently from the repository-level
`artifacts/resume/main.json`. The backend Docker image copies that same tracked
artifact from the repository-root build context, so local resume generation and
hosted bootstrap do not maintain duplicate JSON sources. Application facts stay
in Profile; Answers contains only user-created long-form responses. Unknown or
optional EEO values are not inferred.

## Jobs boundary

`/v1/jobs/*` owns profile editing, listing filtering, application preparation,
document review, answer-library management, synchronous autofill runs, failure
history, and task leasing.
The scheduled SpeedyApply importer reads only the US `README.md` internship files
from the 2027 SWE and AI/ML repositories. It accepts visible `City, ST`, US-wide,
and US remote forms, rejects visibly international primary locations and titles
containing `PhD` or `Ph.D.`, runs at
13:00 UTC, fetches both files concurrently, and batches its database upserts. The
browser cannot trigger a source sync.
An empty parse fails before database mutation so an upstream format change cannot
silently close the entire listing set.

Listings retain their FAANG+, Quant, or general source section and normalized
location facets. The API always limits discovery to internships/co-ops and orders
them newest-first. Selected locations use match-any semantics. The direct
company/ATS URL is used for application work while the GitHub file remains the
discovery URL.

The signed-in CLI creates an autofill run from a posting ID. FastAPI upserts the
application and returns only that user's direct URL, posting context, profile,
approved answer bases, scoped variants, and duplicate-company warnings. Step
results and approved final answers return through authenticated endpoints.

Failure signatures deduplicate issues while occurrence rows preserve run/step
evidence. FastAPI alone writes sanitized artifacts to the private autofill
bucket. It verifies ownership for upload and download; storage paths are never
trusted as authorization. Submission and submitted status remain manual.
