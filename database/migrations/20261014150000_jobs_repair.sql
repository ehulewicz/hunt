-- Repair (design §10.3, §12): the failure issues become the repair queue. A
-- fill that stopped at `format` (the fact fits no option) or `strategy` (the
-- widget would not take the value) is retried in an isolated browser context;
-- what worked is stored as a candidate rule, and it reaches live fills only
-- after you watch it work in `os hunt validate`.

alter table app.jobs_autofill_failure_issues
  add column stopped_at text check (stopped_at in ('format', 'strategy')),
  add column candidate jsonb check (candidate is null or jsonb_typeof(candidate) = 'object'),
  add column repaired_at timestamptz,
  add column validated_at timestamptz;

alter table app.jobs_autofill_failure_issues
  drop constraint jobs_autofill_failure_issues_status_check;
alter table app.jobs_autofill_failure_issues
  add constraint jobs_autofill_failure_issues_status_check check (status in (
    'open', 'diagnosed', 'patch_ready', 'replay_passed', 'live_verified', 'fixed', 'archived',
    'repaired', 'validated'
  ));

-- A repaired issue always carries the rule that repaired it.
alter table app.jobs_autofill_failure_issues
  add constraint jobs_autofill_failure_issues_repair_has_candidate
  check (status not in ('repaired', 'validated') or candidate is not null);

create index jobs_autofill_failure_issues_repair_idx
on app.jobs_autofill_failure_issues (user_id, status, stopped_at)
where stopped_at is not null;
