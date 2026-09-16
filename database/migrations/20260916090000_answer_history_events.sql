-- Autofill runs record every changed field value as history on the
-- application's question rows. Each row is one event of a run: `observed`
-- rows arrive step by step and `final` rows arrive when the run completes.
-- History never writes to jobs_answer_library; approval stays with the user.

alter table app.jobs_application_questions
  add column run_id uuid references app.jobs_autofill_runs(id) on delete set null,
  add column step_number integer,
  add column page_url text,
  add column control_kind text,
  add column field_fingerprint text,
  add column resolver_id text,
  add column proposed_value text,
  add column event_index integer,
  add column event_kind text,
  add constraint jobs_application_questions_step_number_check
    check (step_number is null or step_number >= 0),
  add constraint jobs_application_questions_event_index_check
    check (event_index is null or event_index >= 0),
  add constraint jobs_application_questions_event_kind_check
    check (event_kind is null or event_kind in ('observed', 'final'));

-- Nulls are distinct, so only rows carrying both a run and an event index
-- are held unique; a retried step upserts with `on conflict do nothing`.
-- The leading run_id column also serves the foreign key.
create unique index jobs_application_questions_run_event_idx
on app.jobs_application_questions (run_id, event_index);
