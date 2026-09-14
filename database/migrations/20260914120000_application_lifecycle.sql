-- Applications carry the lifecycle the user tracks; the task worker that
-- prepared and autofilled them is retired, so its queue and tailoring
-- records go with it. `os hunt apply` drives the browser directly.

alter table app.jobs_applications
  drop constraint jobs_applications_status_check;

update app.jobs_applications
set status = case status
  when 'preparing' then 'queued'
  when 'ready' then 'queued'
  when 'submitted' then 'applied'
  when 'interviewing' then 'interview'
  when 'withdrawn' then 'archived'
  else status
end;

alter table app.jobs_applications
  alter column status set default 'queued';

alter table app.jobs_applications
  add constraint jobs_applications_status_check
  check (status in (
    'queued', 'applied', 'oa', 'oa_completed', 'interview', 'offer', 'rejected', 'archived'
  ));

alter table app.jobs_application_questions
  drop constraint jobs_application_questions_source_check;

alter table app.jobs_application_questions
  add constraint jobs_application_questions_source_check
  check (answer_source is null or answer_source in (
    'jobs_profile', 'answer_library', 'model', 'pattern', 'manual'
  ));

drop table app.jobs_tailoring_runs;
drop table app.worker_tasks;
