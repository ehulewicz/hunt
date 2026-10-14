-- Fill policy (design §8, §9). Values now reach forms exactly as stored, so two
-- facts are put right before the next application; both stay unverified until
-- the facts session.

-- The profile column this fact was seeded from truncated the country to
-- "United Stat"; the old resolvers papered over it at fill time.
update app.jobs_facts
set value = '"United States"'::jsonb
where key = 'address.country' and value = '"United Stat"'::jsonb;

-- The office selection policy decided in design §9 (R17-R20), as the fact the
-- office formatter reads.
insert into app.jobs_facts (user_id, key, label, value, value_type)
select users.user_id, 'preferences.location_strategy', 'Office selection policy (design §9)',
  '{
    "tier_1_preferred_cities": [
      {"city": "Chicago", "state": "IL"},
      {"city": "Atlanta", "state": "GA"},
      {"city": "New York", "state": "NY"},
      {"city": "San Francisco", "state": "CA"},
      {"city": "Seattle", "state": "WA"}
    ],
    "tier_2_state_fallbacks": ["IL", "GA", "NY", "CA"],
    "tier_3_region_fallbacks": {
      "preferred_region": "US-East",
      "acceptable_regions": ["US-Midwest", "US-East", "US-West"]
    }
  }'::jsonb,
  'object'
from (select distinct user_id from app.jobs_facts) users
on conflict (user_id, key) do nothing;
