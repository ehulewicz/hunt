-- Personal OS minimal destructive baseline.
-- The hosted project is intentionally reset in place; managed Supabase schemas stay intact.

drop schema if exists agent cascade;
drop schema if exists career cascade;
drop schema if exists content cascade;
drop schema if exists core cascade;
drop schema if exists identity cascade;
drop schema if exists ingest cascade;
drop schema if exists learning cascade;
drop schema if exists life cascade;
drop schema if exists search cascade;
drop schema if exists app cascade;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'personal_os_public_reader') then
    execute 'drop owned by personal_os_public_reader';
    execute 'drop role personal_os_public_reader';
  end if;

  if not exists (select 1 from pg_roles where rolname = 'personal_os_api') then
    create role personal_os_api;
  end if;
end
$$;

alter role personal_os_api
  nologin
  noinherit
  nocreatedb
  nocreaterole
  noreplication
  nobypassrls;

grant personal_os_api to postgres;

create schema app authorization postgres;
revoke all on schema app from public, anon, authenticated, service_role;
grant usage on schema app to personal_os_api;

create function app.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

revoke all on function app.set_updated_at() from public, anon, authenticated, service_role;
grant execute on function app.set_updated_at() to personal_os_api;

create table app.oauth_connections (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  provider text not null,
  provider_account_id text,
  provider_account_email text,
  scopes text[] not null default '{}',
  status text not null default 'connected',
  secret_ref text,
  access_token_expires_at timestamptz,
  connected_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint oauth_connections_provider_check check (provider in ('google')),
  constraint oauth_connections_status_check check (status in ('connected', 'disconnected', 'error')),
  constraint oauth_connections_account_unique unique (user_id, provider, provider_account_id)
);

create table app.worker_tasks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  domain text not null,
  task_type text not null,
  payload jsonb not null default '{}',
  status text not null default 'queued',
  claimed_by text,
  claimed_at timestamptz,
  lease_expires_at timestamptz,
  heartbeat_at timestamptz,
  attempts integer not null default 0,
  max_attempts integer not null default 3,
  available_at timestamptz not null default now(),
  result jsonb,
  error_message text,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint worker_tasks_status_check check (status in ('queued', 'running', 'completed', 'failed', 'cancelled')),
  constraint worker_tasks_attempts_check check (attempts >= 0),
  constraint worker_tasks_max_attempts_check check (max_attempts > 0),
  constraint worker_tasks_payload_object_check check (jsonb_typeof(payload) = 'object'),
  constraint worker_tasks_result_object_check check (result is null or jsonb_typeof(result) = 'object')
);

create table app.jobs_profile (
  user_id uuid primary key references auth.users(id),
  legal_name text,
  preferred_name text,
  email text,
  phone text,
  city text,
  region text,
  country text,
  linkedin_url text,
  github_url text,
  portfolio_url text,
  work_authorization_status text,
  visa_status text,
  sponsorship_required boolean,
  remote_preference text,
  willing_to_relocate boolean,
  target_roles text[],
  target_locations text[],
  minimum_salary numeric,
  professional_summary text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table app.jobs_companies (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  name text not null,
  website_url text,
  careers_url text,
  linkedin_url text,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint jobs_companies_user_name_unique unique (user_id, name)
);

create table app.jobs_postings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  company_id uuid references app.jobs_companies(id),
  title text not null,
  location text,
  employment_type text,
  workplace_type text,
  source text,
  source_url text,
  external_id text,
  description_text text,
  requirements_text text,
  salary_text text,
  status text not null default 'saved',
  discovered_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint jobs_postings_status_check check (status in ('saved', 'reviewing', 'applying', 'applied', 'closed', 'archived'))
);

create table app.jobs_applications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  posting_id uuid not null references app.jobs_postings(id),
  status text not null default 'preparing',
  applied_at timestamptz,
  next_action text,
  next_action_at timestamptz,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint jobs_applications_status_check check (status in ('preparing', 'ready', 'submitted', 'interviewing', 'offer', 'rejected', 'withdrawn', 'archived')),
  constraint jobs_applications_user_posting_unique unique (user_id, posting_id)
);

create table app.jobs_documents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  application_id uuid references app.jobs_applications(id),
  document_type text not null,
  title text not null,
  family_id uuid not null default gen_random_uuid(),
  version integer not null default 1,
  source_document_id uuid references app.jobs_documents(id),
  content_text text,
  storage_path text,
  mime_type text,
  status text not null default 'draft',
  is_current boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint jobs_documents_type_check check (document_type in ('master_resume', 'tailored_resume', 'cover_letter', 'job_description', 'supporting_document')),
  constraint jobs_documents_status_check check (status in ('draft', 'approved', 'archived')),
  constraint jobs_documents_version_check check (version > 0),
  constraint jobs_documents_application_check check (
    (document_type = 'master_resume' and application_id is null)
    or (document_type in ('tailored_resume', 'cover_letter') and application_id is not null)
    or document_type in ('job_description', 'supporting_document')
  ),
  constraint jobs_documents_family_version_unique unique (family_id, version)
);

create table app.jobs_answer_library (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  key text not null,
  normalized_question text,
  answer_text text not null,
  answer_type text not null default 'text',
  approved boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint jobs_answer_library_user_key_unique unique (user_id, key)
);

create table app.jobs_application_questions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  application_id uuid not null references app.jobs_applications(id),
  question_text text not null,
  normalized_key text,
  answer_text text,
  answer_source text,
  answer_library_id uuid references app.jobs_answer_library(id),
  status text not null default 'unanswered',
  display_order integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint jobs_application_questions_source_check check (answer_source is null or answer_source in ('jobs_profile', 'answer_library', 'model', 'manual')),
  constraint jobs_application_questions_status_check check (status in ('unanswered', 'drafted', 'approved', 'submitted'))
);

create table app.jobs_tailoring_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  application_id uuid not null references app.jobs_applications(id),
  worker_task_id uuid references app.worker_tasks(id),
  run_type text not null,
  status text not null default 'queued',
  provider text,
  model text,
  prompt_version text,
  input_document_id uuid references app.jobs_documents(id),
  output_document_id uuid references app.jobs_documents(id),
  error_message text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  constraint jobs_tailoring_runs_type_check check (run_type in ('resume', 'cover_letter', 'application_answer')),
  constraint jobs_tailoring_runs_status_check check (status in ('queued', 'running', 'completed', 'failed', 'cancelled'))
);

create index oauth_connections_user_id_idx on app.oauth_connections (user_id);
create index worker_tasks_user_id_idx on app.worker_tasks (user_id);
create index worker_tasks_claim_idx on app.worker_tasks (status, available_at, created_at)
  where status = 'queued';
create index worker_tasks_lease_idx on app.worker_tasks (lease_expires_at)
  where status = 'running';
create index jobs_companies_user_id_idx on app.jobs_companies (user_id);
create index jobs_postings_user_id_idx on app.jobs_postings (user_id);
create index jobs_postings_company_id_idx on app.jobs_postings (company_id);
create index jobs_applications_user_id_idx on app.jobs_applications (user_id);
create index jobs_applications_posting_id_idx on app.jobs_applications (posting_id);
create index jobs_documents_user_id_idx on app.jobs_documents (user_id);
create index jobs_documents_application_id_idx on app.jobs_documents (application_id);
create index jobs_documents_source_document_id_idx on app.jobs_documents (source_document_id);
create unique index jobs_documents_one_current_per_family_idx on app.jobs_documents (family_id)
  where is_current;
create index jobs_answer_library_user_id_idx on app.jobs_answer_library (user_id);
create index jobs_application_questions_user_id_idx on app.jobs_application_questions (user_id);
create index jobs_application_questions_application_id_idx on app.jobs_application_questions (application_id);
create index jobs_application_questions_answer_library_id_idx on app.jobs_application_questions (answer_library_id);
create index jobs_tailoring_runs_user_id_idx on app.jobs_tailoring_runs (user_id);
create index jobs_tailoring_runs_application_id_idx on app.jobs_tailoring_runs (application_id);
create index jobs_tailoring_runs_worker_task_id_idx on app.jobs_tailoring_runs (worker_task_id);
create index jobs_tailoring_runs_input_document_id_idx on app.jobs_tailoring_runs (input_document_id);
create index jobs_tailoring_runs_output_document_id_idx on app.jobs_tailoring_runs (output_document_id);

create function app.protect_approved_document()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.status = 'approved' then
    raise exception 'approved documents are immutable; create a new version';
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end;
$$;

revoke all on function app.protect_approved_document() from public, anon, authenticated, service_role;
grant execute on function app.protect_approved_document() to personal_os_api;

create trigger jobs_documents_protect_approved
before update or delete on app.jobs_documents
for each row execute function app.protect_approved_document();

create trigger oauth_connections_set_updated_at before update on app.oauth_connections
for each row execute function app.set_updated_at();
create trigger worker_tasks_set_updated_at before update on app.worker_tasks
for each row execute function app.set_updated_at();
create trigger jobs_profile_set_updated_at before update on app.jobs_profile
for each row execute function app.set_updated_at();
create trigger jobs_companies_set_updated_at before update on app.jobs_companies
for each row execute function app.set_updated_at();
create trigger jobs_postings_set_updated_at before update on app.jobs_postings
for each row execute function app.set_updated_at();
create trigger jobs_applications_set_updated_at before update on app.jobs_applications
for each row execute function app.set_updated_at();
create trigger jobs_documents_set_updated_at before update on app.jobs_documents
for each row execute function app.set_updated_at();
create trigger jobs_answer_library_set_updated_at before update on app.jobs_answer_library
for each row execute function app.set_updated_at();
create trigger jobs_application_questions_set_updated_at before update on app.jobs_application_questions
for each row execute function app.set_updated_at();

grant select, insert, update, delete on all tables in schema app to personal_os_api;

alter default privileges for role postgres in schema app revoke all on tables from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema app revoke all on functions from public, anon, authenticated, service_role;
alter default privileges for role postgres in schema app grant select, insert, update, delete on tables to personal_os_api;
