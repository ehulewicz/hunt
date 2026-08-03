# How the database works

The hosted Supabase PostgreSQL database is the only product database. Supabase owns
`auth` and `storage`; Personal OS owns one private `app` schema. The schema is not
listed in the Data API schemas and grants no access to `anon` or `authenticated`.

FastAPI connects with the managed database credential, opens a short transaction,
and immediately assumes the non-login `personal_os_api` role. That role is the only
application role with access to `app`. Every user-owned row references `auth.users`.
The API derives that UUID from a verified JWT and never accepts it in a body.

`migrations/` contains the single active application baseline. Old application
migrations remain as historical evidence in the dated archive and are not runnable.
There is no local Supabase product runtime or seed workflow.
