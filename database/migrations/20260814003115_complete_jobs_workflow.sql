-- Complete the strict-US Jobs workflow and reserve Answers for long-form text.

delete from app.jobs_answer_library
where key in (
  'work_authorization',
  'sponsorship',
  'years_experience',
  'relocation',
  'travel',
  'earliest_start'
);

create index worker_tasks_jobs_application_idx
on app.worker_tasks (
  user_id,
  task_type,
  ((payload ->> 'application_id')),
  status,
  created_at desc
)
where domain = 'jobs';

create unique index worker_tasks_one_active_autofill_per_application_idx
on app.worker_tasks (user_id, ((payload ->> 'application_id')))
where domain = 'jobs'
  and task_type = 'autofill_application'
  and status in ('queued', 'running');

create index jobs_application_questions_unanswered_idx
on app.jobs_application_questions (user_id, application_id, display_order)
where status = 'unanswered';
