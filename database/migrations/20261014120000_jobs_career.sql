-- The career record: the resume as structured rows instead of a JSON file.
-- Dates are dates (month precision; the first of the month), display text is
-- rendered rather than stored, and the facts a form needs (school, tenure,
-- employers, skills) are derived from these rows by the worker. The header's
-- contact details are facts, not rows here. `artifacts/career/main.json` and
-- the PDF become generated exports of this record.

create table app.jobs_education (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  school text not null check (length(trim(school)) > 0),
  location text,
  degree_type text,
  field_of_study text,
  concentrations text,
  coursework text,
  graduation_date date check (graduation_date is null or extract(day from graduation_date) = 1),
  gpa numeric(4, 2) check (gpa is null or gpa >= 0),
  gpa_scale numeric(4, 2) check (gpa_scale is null or gpa_scale > 0),
  is_primary boolean not null default false,
  display_order integer not null check (display_order >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Education facts derive from exactly one row.
create unique index jobs_education_one_primary_idx
on app.jobs_education (user_id) where is_primary;

create table app.jobs_resume_entries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  section text not null check (section in ('experience', 'research', 'project')),
  title text not null check (length(trim(title)) > 0),
  org text,
  location text,
  qualifier text,
  employment_type text check (employment_type in (
    'coop', 'contract', 'founder', 'internship', 'full_time', 'part_time'
  )),
  start_date date check (start_date is null or extract(day from start_date) = 1),
  -- Null with a start date means "Present".
  end_date date check (end_date is null or extract(day from end_date) = 1),
  display_order integer not null check (display_order >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- Only employment has an employment type; research and projects never count
  -- toward tenure.
  check (employment_type is null or section = 'experience'),
  check (start_date is null or end_date is null or start_date <= end_date)
);

create index jobs_resume_entries_user_idx
on app.jobs_resume_entries (user_id, section, display_order);

create table app.jobs_resume_bullets (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  entry_id uuid not null references app.jobs_resume_entries(id) on delete cascade,
  -- AGENTS.md: a bullet never contains a semicolon. Keyword emphasis is
  -- rejected by FastAPI on write; the two-line limit is checked by the build.
  text text not null check (length(trim(text)) > 0 and position(';' in text) = 0),
  display_order integer not null check (display_order >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index jobs_resume_bullets_entry_idx
on app.jobs_resume_bullets (entry_id, display_order);

create table app.jobs_skills (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  category text not null check (length(trim(category)) > 0),
  items text[] not null check (cardinality(items) > 0),
  display_order integer not null check (display_order >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger jobs_education_set_updated_at
before update on app.jobs_education
for each row execute function app.set_updated_at();

create trigger jobs_resume_entries_set_updated_at
before update on app.jobs_resume_entries
for each row execute function app.set_updated_at();

create trigger jobs_resume_bullets_set_updated_at
before update on app.jobs_resume_bullets
for each row execute function app.set_updated_at();

create trigger jobs_skills_set_updated_at
before update on app.jobs_skills
for each row execute function app.set_updated_at();

grant select, insert, update, delete on
  app.jobs_education,
  app.jobs_resume_entries,
  app.jobs_resume_bullets,
  app.jobs_skills
to personal_os_api;

-- The documents table held a copy of the resume seeded on 2026-08-13 plus two
-- tailored resumes; with the career record canonical it is a second writable
-- copy. It is archived in place: renamed, and read-only to the API.
alter table app.jobs_documents rename to jobs_archive_documents;
revoke insert, update, delete on app.jobs_archive_documents from personal_os_api;
