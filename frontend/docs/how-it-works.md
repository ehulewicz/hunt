# How the frontend works

The Vercel-hosted Next.js app is the only browser UI. Dashboard, Jobs, Settings,
and Integrations share the persistent workspace shell. Supabase JS owns the Google
PKCE browser session; the browser sends its access token to FastAPI and never reads
or writes application tables or private Storage directly.

Jobs pages call `/v1/jobs/*` for profile facts, source listings,
applications, documents, long-form answers, and worker credentials. Listings are
the visibly US internships/co-ops from the approved SpeedyApply SWE and AI/ML
READMEs, always newest-first. The only listing filters are All/FAANG+/Quant and a
multi-select normalized-location chooser with match-any behavior. Those controls
and Prepare selected share the first compact bar; there is no page intro, result
count, or table heading. Source sync is
scheduled server-side rather than exposed as a browser action. Application links
open the direct company or ATS page rather than GitHub.

Batch preparation queues worker tasks. Tailored resumes expose authenticated PDF
and structured patch review before approval. Approval automatically queues
autofill; failed or incomplete autofill can be queued again. Application deletion
archives the record and makes its listing selectable. Submitted status is always
a manual user action.
