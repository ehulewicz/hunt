-- The run's `ats` is a label for querying runs and fill events, never a gate:
-- every site is filled from its controls (design §0). Workday joins the labels
-- so its runs stop reading as `generic`.

alter table app.jobs_autofill_runs drop constraint jobs_autofill_runs_ats_check;
alter table app.jobs_autofill_runs
  add constraint jobs_autofill_runs_ats_check
  check (ats in ('greenhouse', 'ashby', 'lever', 'workday', 'generic'));
