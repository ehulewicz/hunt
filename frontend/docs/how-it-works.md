# How the frontend works

The Vercel-hosted Next.js app is the only browser UI. Dashboard, Jobs, Settings,
and Integrations share the persistent workspace shell. Supabase JS owns the Google
PKCE browser session; the browser sends its access token to FastAPI and never reads
or writes application tables or private Storage directly.

Jobs pages call `/v1/jobs/*` for profile facts, source listings,
applications, documents, reusable answers, and worker credentials. Listings start
with every internship/co-op from the approved SpeedyApply SWE and AI/ML sources,
always newest-first. The only listing filters are All/FAANG+/Quant and a
multi-select normalized-location chooser with match-any behavior. Source sync is
scheduled server-side rather than exposed as a browser action. Application links
open the direct company or ATS page rather than GitHub.

Batch preparation queues worker tasks. Tailored resumes and cover letters remain
drafts until reviewed and approved. Submitted status is a manual user action. The
frontend never claims that an application was submitted based on worker activity.
