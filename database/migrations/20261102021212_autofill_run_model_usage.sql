-- The run's model usage by lane. Each autofill step reports the run's
-- cumulative per-lane call and token totals; the latest step's value is the
-- run's. Lanes are pattern, audit, draft and code; pattern and code together
-- are learning.

alter table app.jobs_autofill_runs
  add column model_usage jsonb not null default '{}'::jsonb;

comment on column app.jobs_autofill_runs.model_usage is
  'Per-lane model calls and tokens for this run, latest step''s report';
