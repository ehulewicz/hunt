-- Jobs vertical slice: profile, inclusive listing discovery, worker credentials,
-- tailoring audit data, and private document storage.

alter table app.jobs_profile
  add column address_line_1 text,
  add column address_line_2 text,
  add column postal_code text,
  add column school_name text,
  add column degree_type text,
  add column field_of_study text,
  add column graduation_date date,
  add column gpa numeric,
  add column earliest_start_date date,
  add column earliest_start_text text,
  add column years_professional_experience numeric,
  add column willing_to_travel boolean,
  add column target_career_stages text[] not null default array['internship']::text[],
  add column eeo_gender_identity text,
  add column eeo_race_ethnicities text[] not null default '{}',
  add column eeo_veteran_status text,
  add column eeo_disability_status text,
  add column completed_at timestamptz,
  add constraint jobs_profile_gpa_check check (gpa is null or (gpa >= 0 and gpa <= 5)),
  add constraint jobs_profile_experience_check check (
    years_professional_experience is null or years_professional_experience >= 0
  ),
  add constraint jobs_profile_target_stages_check check (
    target_career_stages <@ array['internship', 'new_grad']::text[]
  );

alter table app.jobs_postings
  add column application_url text,
  add column discovery_url text,
  add column role_families text[] not null default '{}',
  add column career_stage text,
  add column feed_region text,
  add column source_posted_at date,
  add column source_age_days integer,
  add column last_seen_at timestamptz,
  add constraint jobs_postings_stage_check check (
    career_stage is null or career_stage in ('internship', 'new_grad')
  ),
  add constraint jobs_postings_region_check check (
    feed_region is null or feed_region in ('usa', 'international')
  ),
  add constraint jobs_postings_source_age_check check (
    source_age_days is null or source_age_days >= 0
  );

create unique index jobs_postings_source_external_unique_idx
on app.jobs_postings (user_id, source, external_id)
where source is not null and external_id is not null;

create index jobs_postings_daily_idx
on app.jobs_postings (user_id, career_stage, source_posted_at desc, created_at desc);

create index jobs_postings_role_families_idx
on app.jobs_postings using gin (role_families);

create unique index jobs_documents_one_current_master_resume_idx
on app.jobs_documents (user_id)
where document_type = 'master_resume' and is_current;

alter table app.jobs_tailoring_runs
  add column changed_word_ratio numeric,
  add column diff_json jsonb,
  add column validation_json jsonb,
  add constraint jobs_tailoring_runs_change_ratio_check check (
    changed_word_ratio is null or (changed_word_ratio >= 0 and changed_word_ratio <= 1)
  ),
  add constraint jobs_tailoring_runs_diff_object_check check (
    diff_json is null or jsonb_typeof(diff_json) = 'object'
  ),
  add constraint jobs_tailoring_runs_validation_object_check check (
    validation_json is null or jsonb_typeof(validation_json) = 'object'
  );

create table app.jobs_worker_credentials (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id),
  name text not null,
  token_hash text not null,
  scopes text[] not null default array['jobs:worker']::text[],
  expires_at timestamptz not null,
  last_used_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint jobs_worker_credentials_name_check check (length(trim(name)) between 1 and 80),
  constraint jobs_worker_credentials_token_hash_unique unique (token_hash),
  constraint jobs_worker_credentials_scopes_check check (
    scopes <@ array['jobs:worker']::text[] and cardinality(scopes) > 0
  )
);

create index jobs_worker_credentials_user_id_idx
on app.jobs_worker_credentials (user_id, created_at desc);

create trigger jobs_worker_credentials_set_updated_at
before update on app.jobs_worker_credentials
for each row execute function app.set_updated_at();

grant select, insert, update, delete on app.jobs_worker_credentials to personal_os_api;

-- Approved document content is immutable. Lifecycle-only changes are allowed so
-- a newer approved version can become current without mutating the old artifact.
create or replace function app.protect_approved_document()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' and old.status = 'approved' then
    raise exception 'approved documents are immutable; archive them instead';
  end if;

  if tg_op = 'UPDATE' and old.status = 'approved' and (
    new.user_id is distinct from old.user_id
    or new.application_id is distinct from old.application_id
    or new.document_type is distinct from old.document_type
    or new.title is distinct from old.title
    or new.family_id is distinct from old.family_id
    or new.version is distinct from old.version
    or new.source_document_id is distinct from old.source_document_id
    or new.content_text is distinct from old.content_text
    or new.storage_path is distinct from old.storage_path
    or new.mime_type is distinct from old.mime_type
    or new.created_at is distinct from old.created_at
  ) then
    raise exception 'approved document content is immutable; create a new version';
  end if;

  return case when tg_op = 'DELETE' then old else new end;
end;
$$;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'jobs-documents',
  'jobs-documents',
  false,
  5242880,
  array['application/pdf']::text[]
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

revoke all on app.jobs_worker_credentials from public, anon, authenticated, service_role;
