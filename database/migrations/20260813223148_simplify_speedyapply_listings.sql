-- Simplify Jobs discovery around SpeedyApply's internship feeds.

alter table app.jobs_postings
  add column source_category text not null default 'other',
  add column normalized_locations text[] not null default '{}',
  add constraint jobs_postings_source_category_check check (
    source_category in ('faang', 'quant', 'other')
  );

update app.jobs_postings
set source = 'speedyapply/college-jobs'
where source = 'speedyapply/2027-SWE-College-Jobs';

create index jobs_postings_internship_category_newest_idx
on app.jobs_postings (
  user_id,
  career_stage,
  source_category,
  source_age_days,
  source_posted_at desc,
  created_at desc
)
where status <> 'archived';

create index jobs_postings_normalized_locations_idx
on app.jobs_postings using gin (normalized_locations);
