# How workers work

There is no worker runtime in this milestone. The package has a help/version CLI so
the boundary is concrete without implying polling, Claude, Playwright, or document
generation exists. Future workers will call FastAPI only and will never connect to
Supabase directly.
