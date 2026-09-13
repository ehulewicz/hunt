-- Pattern rules: normalized_question holds a case-insensitive regular
-- expression and answer_text the answer used when exactly one rule matches.

alter table app.jobs_answer_library
  drop constraint jobs_answer_library_reuse_policy_check;

alter table app.jobs_answer_library
  add constraint jobs_answer_library_reuse_policy_check
  check (reuse_policy in ('exact', 'contextual', 'pattern'));
