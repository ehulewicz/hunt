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

## Test ports

There is no local product stack. Short-lived test processes may use:

| Process | Port | Purpose |
|---|---:|---|
| Next.js | 3000 | frontend browser and visual tests |
| FastAPI | 8000 | backend HTTP tests when a real process is required |

## One-time Google login bootstrap

To create the missing provider credentials:

1. Open Google Auth Platform for GCP project `example-gcp-project`.
2. Configure the app as **Personal OS** with `eahtree@gmail.com` as the support
   contact. For an External app in testing, add `eahtree@gmail.com` as a test
   user.
3. Keep Auth data access to `openid`, `userinfo.email`, and `userinfo.profile`.
4. Create a **Web application** OAuth client with:
   - Authorized JavaScript origin:
     `https://personal-os-ten-kappa.vercel.app`
   - Authorized redirect URI:
     `https://exampleprojectref.supabase.co/auth/v1/callback`
5. Save the client ID and secret immediately. Google only displays a newly
   created client secret once.
6. From an authenticated machine, create the two expected Secret Manager
   secrets without putting their values in shell history:

```bash
read -r -p "Google OAuth client ID: " GOOGLE_OAUTH_CLIENT_ID
read -r -s -p "Google OAuth client secret: " GOOGLE_OAUTH_CLIENT_SECRET
printf '\n'

printf '%s' "$GOOGLE_OAUTH_CLIENT_ID" |
  gcloud secrets create personal-os-google-oauth-client-id \
    --project=example-gcp-project \
    --replication-policy=automatic \
    --data-file=-
printf '%s' "$GOOGLE_OAUTH_CLIENT_SECRET" |
  gcloud secrets create personal-os-google-oauth-client-secret \
    --project=example-gcp-project \
    --replication-policy=automatic \
    --data-file=-
unset GOOGLE_OAUTH_CLIENT_ID GOOGLE_OAUTH_CLIENT_SECRET
```

7. Grant the GitHub deployer read access to only the two new secrets:

```bash
gcloud secrets add-iam-policy-binding personal-os-google-oauth-client-id \
  --project=example-gcp-project \
  --member=serviceAccount:personal-os-deployer@example-gcp-project.iam.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor
gcloud secrets add-iam-policy-binding personal-os-google-oauth-client-secret \
  --project=example-gcp-project \
  --member=serviceAccount:personal-os-deployer@example-gcp-project.iam.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor
```

8. Run and watch the production workflow:

```bash
gh workflow run Deploy
gh run watch --exit-status
```

The workflow reads the two secrets, runs `supabase config push`, and then
deploys the API. Do not consider the bootstrap complete until hosted Auth
reports Google enabled and global signup disabled, and the login/bootstrap/
Dashboard/logout acceptance pass succeeds for the approved account.
