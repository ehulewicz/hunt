-- Changes: every change to a pattern or a fact, and every code change the
-- maintenance path lands, with what it was before and after. A change that was
-- learned is replayed offline against the recorded sightings it can affect
-- before it is kept; its verification says how many it replayed and how many
-- it made wrong. A change you make is recorded and not replayed. The API
-- writes a pattern or fact change in the same transaction as the change.

create table app.jobs_changes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  subject text not null check (subject in ('pattern', 'fact', 'code')),
  -- The pattern's id, the fact's key, or the commit a code change landed in.
  subject_id text not null check (length(subject_id) between 1 and 200),
  action text not null check (action in ('create', 'update', 'retire', 'reject', 'delete')),
  before jsonb,
  after jsonb,
  origin text not null check (origin in ('you', 'inbox', 'model', 'repair')),
  -- The failure it answered: the run, and the question that failed in it.
  run_id uuid references app.jobs_autofill_runs(id) on delete set null,
  question_key text check (question_key is null or length(question_key) > 0),
  -- Null when nothing verifies it; pending until a replay reports.
  status text check (status in ('pending', 'passed', 'failed')),
  sampled integer check (sampled is null or sampled >= 0),
  failed integer check (failed is null or failed >= 0),
  created_at timestamptz not null default now(),
  verified_at timestamptz,
  -- A verified change has its counts, and it failed exactly when it made one wrong.
  constraint jobs_changes_verification check (
    case when coalesce(status, 'pending') = 'pending'
      then verified_at is null and sampled is null and failed is null
      else verified_at is not null and sampled is not null and failed is not null
           and failed <= sampled
           and (status = 'failed') = (failed > 0)
    end
  )
);

create index jobs_changes_subject_idx
  on app.jobs_changes (user_id, subject, subject_id, created_at desc);
create index jobs_changes_run_idx on app.jobs_changes (run_id) where run_id is not null;

grant select, insert, update on app.jobs_changes to personal_os_api;
