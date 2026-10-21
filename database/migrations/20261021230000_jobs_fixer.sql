-- The fixer: a run fixes itself. A failure the existing lanes cannot settle goes
-- to a model that writes code in a scratch clone; the code is tested, replayed,
-- tried on the live control, and committed, and the run goes on with it. The
-- after-the-fact maintenance statuses (diagnosed, patch_ready, replay_passed,
-- live_verified) are retired with the commands that set them.

-- An issue is open, being fixed now, fixed by a commit, or unsolved once its
-- attempts ran out; repaired and validated are the data repair lane's.
update app.jobs_autofill_failure_issues
  set status = 'open'
  where status in ('diagnosed', 'patch_ready', 'replay_passed', 'live_verified');
alter table app.jobs_autofill_failure_issues
  drop constraint jobs_autofill_failure_issues_status_check;
alter table app.jobs_autofill_failure_issues
  add constraint jobs_autofill_failure_issues_status_check check (status in (
    'open', 'fixing', 'fixed', 'unsolved', 'archived', 'repaired', 'validated'
  ));

-- Every change says who made it (the worker itself among them), the failure
-- issue it fixed, and covers prose, question dispositions, and validated
-- strategy preferences as well as patterns, facts, and code.
alter table app.jobs_changes drop constraint jobs_changes_subject_check;
alter table app.jobs_changes add constraint jobs_changes_subject_check
  check (subject in ('pattern', 'fact', 'code', 'prose', 'question', 'strategy'));
alter table app.jobs_changes drop constraint jobs_changes_origin_check;
alter table app.jobs_changes add constraint jobs_changes_origin_check
  check (origin in ('you', 'inbox', 'model', 'repair', 'worker'));
alter table app.jobs_changes
  add column failure_issue_id uuid
    references app.jobs_autofill_failure_issues(id) on delete set null;
create index jobs_changes_failure_issue_idx
  on app.jobs_changes (failure_issue_id) where failure_issue_id is not null;

-- A control kind or strategy the fixer adds is recorded like the seven seeded
-- ones: a kind is a name, not a fixed list.
alter table app.jobs_controls drop constraint jobs_controls_kind_check;
alter table app.jobs_controls add constraint jobs_controls_kind_check
  check (kind ~ '^[a-z][a-z0-9_]{1,40}$');
alter table app.jobs_strategies drop constraint jobs_strategies_control_kind_check;
alter table app.jobs_strategies add constraint jobs_strategies_control_kind_check
  check (control_kind ~ '^[a-z][a-z0-9_]{1,40}$');

-- How many model calls a run made: a site whose last run made none fills
-- without a model.
alter table app.jobs_autofill_runs
  add column model_calls integer not null default 0 check (model_calls >= 0);
