# How the frontend works

The Vercel-hosted Next.js app is the only browser UI. Dashboard, Jobs, Settings,
and Integrations share the persistent workspace shell. Supabase JS owns the Google
PKCE browser session; the browser sends its access token to FastAPI and never reads
or writes application tables or private Storage directly.

Jobs pages call `/v1/jobs/*` for profile facts, inclusive source listings,
applications, documents, reusable answers, and worker credentials. Listings start
with internship and daily filters because that is the normal workflow, but neither
filter affects ingestion. Role-family filters include SWE, data engineering, ML
engineering, data science, and `other`; adjacent titles such as full-stack remain
visible. Application links open the direct company or ATS page rather than GitHub.

Batch preparation queues worker tasks. Tailored resumes and cover letters remain
drafts until reviewed and approved. Submitted status is a manual user action. The
frontend never claims that an application was submitted based on worker activity.
