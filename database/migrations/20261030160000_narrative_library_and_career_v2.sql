-- The narrative library and career record v2. The library is Ethan's
-- handwritten writing that long-form answers are composed from: entries keyed
-- by a code, optionally bound to the resume entry they back. A prose gap
-- records a long-form question the library covers only partly or not at all,
-- so the Inbox can hold it until the writing exists. The career record learns
-- what is printed: entries, bullets, and schools each carry `on_resume`,
-- education gains a level and a coursework list, and activities join the entry
-- sections. Resume entry rows become stable (updated in place instead of
-- deleted and reinserted) so a library entry's link survives a save. Change
-- history gains `library` as a subject.

alter table app.jobs_resume_entries
  add column on_resume boolean not null default true;

alter table app.jobs_resume_entries
  drop constraint jobs_resume_entries_section_check;
alter table app.jobs_resume_entries
  add constraint jobs_resume_entries_section_check
  check (section in ('experience', 'research', 'project', 'activity'));

alter table app.jobs_resume_bullets
  add column on_resume boolean not null default true;

alter table app.jobs_education
  add column level text not null default 'undergraduate'
    check (level in ('high_school', 'undergraduate', 'graduate')),
  add column on_resume boolean not null default true;

-- Coursework becomes a list: split on commas, trim, drop empties, keep order.
alter table app.jobs_education
  alter column coursework type text[]
  using coalesce(
    array_remove(regexp_split_to_array(trim(coursework), '\s*,\s*'), ''),
    array[]::text[]
  ),
  alter column coursework set default array[]::text[],
  alter column coursework set not null;

-- One library entry per code (1.1, 2.2.11, 5.1.E2, 6.1.P3); `entry_id` is the
-- resume entry it backs and survives that entry's edits but not its deletion.
create table app.jobs_library_entries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  code text not null check (length(trim(code)) > 0),
  section text not null check (section in (
    'interest', 'direction', 'evidence', 'story', 'entry', 'note'
  )),
  entry_id uuid references app.jobs_resume_entries(id) on delete set null,
  subject text,
  prompt text not null check (length(trim(prompt)) > 0),
  guidance text not null default '',
  answer text,
  display_order integer not null check (display_order >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, code)
);

create index jobs_library_entries_entry_idx
on app.jobs_library_entries (entry_id) where entry_id is not null;

-- A question whose saved prose does not cover what it asks: partially, or not
-- at all. The open gap for a question is singular; resolving it frees the next.
create table app.jobs_prose_gaps (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  question_id uuid not null references app.jobs_questions(id) on delete cascade,
  application_id uuid references app.jobs_applications(id) on delete set null,
  family text not null,
  coverage text not null check (coverage in ('partial', 'missing')),
  missing_detail text not null default '',
  created_at timestamptz not null default now(),
  resolved_at timestamptz
);

create unique index jobs_prose_gaps_open_idx
  on app.jobs_prose_gaps (user_id, question_id) where resolved_at is null;

create index jobs_prose_gaps_question_idx
  on app.jobs_prose_gaps (question_id);

create trigger jobs_library_entries_set_updated_at
before update on app.jobs_library_entries
for each row execute function app.set_updated_at();

grant select, insert, update, delete on app.jobs_library_entries to personal_os_api;
grant select, insert, update on app.jobs_prose_gaps to personal_os_api;

-- A library write is history like any other.
alter table app.jobs_changes drop constraint jobs_changes_subject_check;
alter table app.jobs_changes add constraint jobs_changes_subject_check
  check (subject in ('pattern', 'fact', 'code', 'prose', 'question', 'strategy', 'library'));
