-- Queueing is retired. `applying` is the transient label for a browser attempt;
-- attempts that end without a submission are archived by the application flow.

alter table app.jobs_applications
  drop constraint jobs_applications_status_check;

update app.jobs_applications
set status = 'archived', updated_at = now()
where status = 'queued';

alter table app.jobs_applications
  alter column status set default 'applying';

alter table app.jobs_applications
  add constraint jobs_applications_status_check
  check (status in (
    'applying', 'applied', 'oa', 'oa_completed', 'interview', 'offer',
    'rejected', 'skipped', 'archived'
  ));
