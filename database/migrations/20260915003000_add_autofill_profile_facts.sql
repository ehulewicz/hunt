alter table app.jobs_profile
  add column graduation_term text,
  add column completed_coop_terms numeric,
  add column minimum_salary_period text,
  add constraint jobs_profile_graduation_term_check
    check (graduation_term in ('Spring', 'Summer', 'Fall', 'Winter')),
  add constraint jobs_profile_completed_coop_terms_check
    check (completed_coop_terms >= 0),
  add constraint jobs_profile_minimum_salary_period_check
    check (minimum_salary_period in ('hour', 'year'));

update app.jobs_profile
set graduation_term = 'Fall',
    completed_coop_terms = 4.5,
    minimum_salary = 35,
    minimum_salary_period = 'hour',
    updated_at = now();
