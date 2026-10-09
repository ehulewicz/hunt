-- Facts: one row per thing that is true about the user, keyed by a stable
-- dotted name. Replaces jobs_profile as the source the worker fills from;
-- jobs_profile is dropped once every seeded row has been verified.
-- Time-dependent facts (completed co-op rotations, graduation term) are
-- derived in the worker and never stored here.

create table app.jobs_facts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  key text not null
    check (key ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
  label text not null check (length(trim(label)) > 0),
  value jsonb not null,
  value_type text not null
    check (value_type in ('text', 'boolean', 'number', 'date', 'choice', 'list', 'object')),
  sensitive boolean not null default false,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, key),
  constraint jobs_facts_value_matches_type check (
    case value_type
      when 'text' then jsonb_typeof(value) = 'string'
      when 'choice' then jsonb_typeof(value) = 'string'
      when 'date' then jsonb_typeof(value) = 'string'
        and (value #>> '{}') ~ '^\d{4}-\d{2}-\d{2}$'
      when 'boolean' then jsonb_typeof(value) = 'boolean'
      when 'number' then jsonb_typeof(value) = 'number'
      when 'list' then jsonb_typeof(value) = 'array'
      when 'object' then jsonb_typeof(value) = 'object'
    end
  )
);

create trigger jobs_facts_set_updated_at
before update on app.jobs_facts
for each row execute function app.set_updated_at();

grant select, insert, update, delete on app.jobs_facts to personal_os_api;

-- Seed from the profile, unverified. Columns that become derived
-- (graduation_term, completed_coop_terms) or prose (professional_summary),
-- and markers that are not facts (completed_at), are not copied.
insert into app.jobs_facts (user_id, key, label, value, value_type, sensitive)
select profile.user_id, fact.key, fact.label, fact.value, fact.value_type, fact.sensitive
from app.jobs_profile profile
cross join lateral (
  values
    ('identity.legal_name', 'Full legal name',
      to_jsonb(profile.legal_name), 'text', false),
    ('identity.preferred_name', 'Preferred first name',
      to_jsonb(profile.preferred_name), 'text', false),
    ('contact.email', 'Email address',
      to_jsonb(profile.email), 'text', false),
    ('contact.phone', 'Phone number',
      to_jsonb(profile.phone), 'text', true),
    ('contact.linkedin_url', 'LinkedIn profile URL',
      to_jsonb(profile.linkedin_url), 'text', false),
    ('contact.github_url', 'GitHub profile URL',
      to_jsonb(profile.github_url), 'text', false),
    ('contact.portfolio_url', 'Portfolio URL',
      to_jsonb(profile.portfolio_url), 'text', false),
    ('address.line_1', 'Street address',
      to_jsonb(profile.address_line_1), 'text', true),
    ('address.line_2', 'Second address line',
      to_jsonb(profile.address_line_2), 'text', true),
    ('address.city', 'Current city',
      to_jsonb(profile.city), 'text', false),
    ('address.region', 'Current state or region',
      to_jsonb(profile.region), 'text', false),
    ('address.postal_code', 'Postal code',
      to_jsonb(profile.postal_code), 'text', true),
    ('address.country', 'Country of residence',
      to_jsonb(profile.country), 'text', false),
    ('work_auth.us_citizen', 'United States citizen',
      to_jsonb(
        profile.work_authorization_status ilike '%citizen%'
        or profile.visa_status ilike '%citizen%'
      ), 'boolean', false),
    ('work_auth.authorized_us', 'Authorized to work in the United States',
      case
        when profile.work_authorization_status ilike '%citizen%'
          or profile.visa_status ilike '%citizen%'
        then 'true'::jsonb
      end, 'boolean', false),
    ('work_auth.requires_sponsorship', 'Requires visa sponsorship now or in the future',
      to_jsonb(profile.sponsorship_required), 'boolean', false),
    ('education.school_name', 'School',
      to_jsonb(profile.school_name), 'text', false),
    ('education.degree_type', 'Degree type',
      to_jsonb(profile.degree_type), 'text', false),
    ('education.field_of_study', 'Field of study',
      to_jsonb(profile.field_of_study), 'text', false),
    ('education.graduation_date', 'Graduation date',
      to_jsonb(profile.graduation_date::text), 'date', false),
    ('education.gpa', 'GPA',
      to_jsonb(profile.gpa), 'number', false),
    ('availability.earliest_start_date', 'Earliest start date',
      to_jsonb(profile.earliest_start_date::text), 'date', false),
    ('availability.notice_period_weeks', 'Notice period before starting, in weeks',
      case
        when profile.earliest_start_text ~* '(\d+)\s*weeks?'
          then to_jsonb(substring(profile.earliest_start_text from '(\d+)\s*weeks?')::numeric)
        when profile.earliest_start_text ~* '\mtwo weeks\M' then '2'::jsonb
      end, 'number', false),
    ('experience.years_professional', 'Years of professional experience',
      to_jsonb(profile.years_professional_experience), 'number', false),
    ('preferences.remote', 'Remote work preference',
      to_jsonb(profile.remote_preference), 'choice', false),
    ('preferences.willing_to_relocate', 'Willing to relocate',
      to_jsonb(profile.willing_to_relocate), 'boolean', false),
    ('preferences.willing_to_travel', 'Willing to travel',
      to_jsonb(profile.willing_to_travel), 'boolean', false),
    ('discovery.target_roles', 'Target roles for discovery',
      nullif(to_jsonb(profile.target_roles), '[]'::jsonb), 'list', false),
    ('discovery.target_locations', 'Target locations for discovery',
      nullif(to_jsonb(profile.target_locations), '[]'::jsonb), 'list', false),
    ('discovery.target_career_stages', 'Target career stages for discovery',
      nullif(to_jsonb(profile.target_career_stages), '[]'::jsonb), 'list', false),
    ('eeo.gender_identity', 'Gender identity',
      to_jsonb(profile.eeo_gender_identity), 'text', true),
    ('eeo.race_ethnicities', 'Race and ethnicity',
      nullif(to_jsonb(profile.eeo_race_ethnicities), '[]'::jsonb), 'list', true),
    ('eeo.veteran_status', 'Veteran status',
      to_jsonb(profile.eeo_veteran_status), 'text', true),
    ('eeo.disability_status', 'Disability status',
      to_jsonb(profile.eeo_disability_status), 'text', true)
) as fact(key, label, value, value_type, sensitive)
where fact.value is not null and fact.value <> 'null'::jsonb;

-- Values the user set directly on 2026-10-08, so they arrive verified:
-- four co-op rotations complete as of the end of summer 2026, and a $40/hour
-- minimum (annual is derived).
insert into app.jobs_facts (user_id, key, label, value, value_type, verified_at)
select profile.user_id, fact.key, fact.label, fact.value::jsonb, 'object', now()
from app.jobs_profile profile
cross join (
  values
    ('coop.anchor', 'Completed co-op rotations as of a date',
      '{"count": 4, "as_of": "2026-08-01"}'),
    ('compensation.minimum', 'Minimum compensation',
      '{"amount": 40, "currency": "USD", "period": "hour"}')
) as fact(key, label, value);
