# Deployment

The production frontend is the Vercel project rooted at `frontend/`. Git pushes to
`main` deploy it through Vercel's Git integration.

GitHub Actions applies `database/migrations/` to the linked hosted Supabase project
and builds `backend/Dockerfile` for the `personal-os-api` Cloud Run service. The
workflow authenticates to GCP with Workload Identity Federation and resolves all
credentials from Secret Manager in project `example-gcp-project`.

Required repository variables are `GCP_PROJECT_ID`, `GCP_REGION`,
`GCP_WIF_PROVIDER`, and `GCP_DEPLOYER_SA`. The database URL, Supabase access token,
and Google OAuth client credentials remain in GCP Secret Manager.
