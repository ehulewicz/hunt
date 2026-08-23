-- Synchronous local Playwright autofill state and failure-learning history.

alter table app.jobs_answer_library
  add column normalized_key text,
  add column reuse_policy text not null default 'exact'
    constraint jobs_answer_library_reuse_policy_check
    check (reuse_policy in ('exact', 'contextual'));

update app.jobs_answer_library
set normalized_key = trim(regexp_replace(lower(coalesce(normalized_question, key)), '[^a-z0-9+#]+', ' ', 'g'))
where normalized_key is null;

alter table app.jobs_answer_library alter column normalized_key set not null;

create unique index jobs_answer_library_normalized_key_unique
on app.jobs_answer_library (user_id, normalized_key)
where answer_type = 'long_form';

create table app.jobs_autofill_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  application_id uuid not null references app.jobs_applications(id) on delete cascade,
  ats text not null check (ats in ('greenhouse', 'ashby', 'lever', 'generic')),
  adapter_version text not null,
  application_url text not null,
  final_url text,
  status text not null default 'running'
    check (status in ('running', 'review_ready', 'abandoned', 'failed')),
  current_step integer not null default 0 check (current_step >= 0),
  filled_count integer not null default 0 check (filled_count >= 0),
  skipped_count integer not null default 0 check (skipped_count >= 0),
  failure_count integer not null default 0 check (failure_count >= 0),
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index jobs_autofill_runs_user_created_idx
on app.jobs_autofill_runs (user_id, created_at desc);

create trigger jobs_autofill_runs_set_updated_at
before update on app.jobs_autofill_runs
for each row execute function app.set_updated_at();

create table app.jobs_autofill_failure_issues (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  signature text not null,
  ats text not null,
  category text not null,
  field_label text,
  control_kind text,
  status text not null default 'open'
    check (status in ('open', 'diagnosed', 'patch_ready', 'replay_passed', 'live_verified', 'fixed')),
  diagnosis text,
  proposed_patch text,
  fixed_commit text,
  verification_notes text,
  occurrence_count integer not null default 1 check (occurrence_count >= 1),
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  replay_verified_at timestamptz,
  live_verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, signature)
);

create index jobs_autofill_failure_issues_user_status_idx
on app.jobs_autofill_failure_issues (user_id, status, last_seen_at desc);

create trigger jobs_autofill_failure_issues_set_updated_at
before update on app.jobs_autofill_failure_issues
for each row execute function app.set_updated_at();

create table app.jobs_autofill_failure_occurrences (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  issue_id uuid not null references app.jobs_autofill_failure_issues(id) on delete cascade,
  run_id uuid not null references app.jobs_autofill_runs(id) on delete cascade,
  step_number integer not null check (step_number >= 0),
  field_fingerprint text,
  intended_value text,
  observed_value text,
  error_message text,
  dom_signals jsonb not null default '{}'::jsonb,
  artifact_paths jsonb not null default '{}'::jsonb,
  adapter_version text not null,
  created_at timestamptz not null default now()
);

create index jobs_autofill_failure_occurrences_issue_idx
on app.jobs_autofill_failure_occurrences (user_id, issue_id, created_at desc);

grant select, insert, update, delete on
  app.jobs_autofill_runs,
  app.jobs_autofill_failure_issues,
  app.jobs_autofill_failure_occurrences
to personal_os_api;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'jobs-autofill-artifacts',
  'jobs-autofill-artifacts',
  false,
  10485760,
  array['image/png', 'text/html', 'text/plain', 'application/json']::text[]
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

revoke all on
  app.jobs_autofill_runs,
  app.jobs_autofill_failure_issues,
  app.jobs_autofill_failure_occurrences
from public, anon, authenticated, service_role;
