-- Answers restart from scratch (design §0, R40-R50). What the old system knew is
-- archived in place and frozen; questions, patterns, and prose start empty and
-- are learned. The archive is consulted by the worker when a question has no
-- active pattern, and it only ever names the fact a question asks about.

-- The answer library and the retired profile become the archive: renamed, and
-- read-only to the API role.
alter table app.jobs_answer_library rename to jobs_archive_answers;
revoke insert, update, delete on app.jobs_archive_answers from personal_os_api;

alter table app.jobs_profile rename to jobs_archive_profile;
revoke insert, update, delete on app.jobs_archive_profile from personal_os_api;

-- Failure issues the old dispatch left open are archived, not deleted (R50). A
-- signature that fails again reopens as a new-era issue.
alter table app.jobs_autofill_failure_issues
  drop constraint jobs_autofill_failure_issues_status_check;
alter table app.jobs_autofill_failure_issues
  add constraint jobs_autofill_failure_issues_status_check check (status in (
    'open', 'diagnosed', 'patch_ready', 'replay_passed', 'live_verified', 'fixed', 'archived'
  ));
update app.jobs_autofill_failure_issues set status = 'archived' where status <> 'fixed';

-- Runs still marked running from before the restart never finished.
update app.jobs_autofill_runs
set status = 'abandoned', completed_at = coalesce(completed_at, now())
where status = 'running' and started_at < now() - interval '1 day';

-- A question is a normalized label, recorded the first time it is met (R44).
create table app.jobs_questions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  normalized_key text not null check (length(normalized_key) > 0),
  sample_label text not null check (length(trim(sample_label)) > 0),
  form text not null default 'short' check (form in ('short', 'long')),
  disposition text not null default 'unbound'
    check (disposition in ('unbound', 'pattern', 'one_off', 'long_form')),
  -- The active binding that answers it, once one is accepted.
  pattern_id uuid,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, normalized_key)
);

-- A pattern connects a short-form question to a fact. A binding answers one
-- question; an expression answers any phrasing its regex matches. Only
-- `active` patterns are read when filling.
create table app.jobs_patterns (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  resolver_id text not null check (length(resolver_id) between 1 and 200),
  kind text not null check (kind in ('expression', 'binding')),
  expression text,
  question_id uuid references app.jobs_questions(id) on delete cascade,
  fact_key text not null check (fact_key ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
  formatter text check (formatter is null or formatter ~ '^[a-z][a-z0-9_]*$'),
  origin text not null check (origin in ('builtin', 'inbox', 'model', 'condensed', 'archive')),
  archive_ref text,
  status text not null default 'candidate' check (status in (
    'candidate', 'repaired', 'validated', 'active', 'rejected', 'retired'
  )),
  superseded_by uuid references app.jobs_patterns(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, resolver_id),
  check ((kind = 'expression') = (expression is not null)),
  check ((kind = 'binding') = (question_id is not null))
);

-- One row per question and fact for bindings, and at most one active binding
-- per question.
create unique index jobs_patterns_binding_idx
on app.jobs_patterns (user_id, question_id, fact_key) where kind = 'binding';
create unique index jobs_patterns_active_binding_idx
on app.jobs_patterns (user_id, question_id) where kind = 'binding' and status = 'active';
create index jobs_patterns_status_idx on app.jobs_patterns (user_id, status);

alter table app.jobs_questions
  add constraint jobs_questions_pattern_id_fkey
  foreign key (pattern_id) references app.jobs_patterns(id) on delete set null;

-- Long-form writing, starting empty. A submitted long-form answer is saved
-- unapproved; the Inbox approves it for reuse.
create table app.jobs_prose (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  question_id uuid references app.jobs_questions(id) on delete set null,
  -- Where it was written, so a company-specific answer is recognizable.
  application_id uuid references app.jobs_applications(id) on delete set null,
  text text not null check (length(trim(text)) > 0),
  approved boolean not null default false,
  origin text not null check (origin in ('manual', 'model', 'archive')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index jobs_prose_question_idx on app.jobs_prose (user_id, question_id);

create trigger jobs_questions_set_updated_at
before update on app.jobs_questions
for each row execute function app.set_updated_at();

create trigger jobs_patterns_set_updated_at
before update on app.jobs_patterns
for each row execute function app.set_updated_at();

create trigger jobs_prose_set_updated_at
before update on app.jobs_prose
for each row execute function app.set_updated_at();

-- Answer history and fill events point at the question they answered, and an
-- event at the pattern that produced its value.
alter table app.jobs_application_questions
  add column question_id uuid references app.jobs_questions(id) on delete set null;
alter table app.jobs_application_questions
  drop constraint jobs_application_questions_source_check;
alter table app.jobs_application_questions
  add constraint jobs_application_questions_source_check check (
    answer_source is null or answer_source in (
      'jobs_profile', 'answer_library', 'model', 'pattern', 'manual', 'archive', 'prose'
    )
  );
create index jobs_application_questions_question_idx
on app.jobs_application_questions (question_id) where question_id is not null;

alter table app.jobs_fill_events
  add column question_id uuid references app.jobs_questions(id) on delete set null,
  add column pattern_id uuid references app.jobs_patterns(id) on delete set null;
create index jobs_fill_events_question_idx on app.jobs_fill_events (user_id, question_id);

grant select, insert, update, delete on
  app.jobs_questions,
  app.jobs_patterns,
  app.jobs_prose
to personal_os_api;
