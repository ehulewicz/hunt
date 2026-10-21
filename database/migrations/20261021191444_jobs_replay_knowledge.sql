-- Capture the exact values used by each attempt, including transient posting facts.
alter table app.jobs_fill_events
  add column knowledge jsonb
  check (knowledge is null or (
    jsonb_typeof(knowledge) = 'object'
    and knowledge ?& array['facts', 'types']
    and jsonb_typeof(knowledge -> 'facts') = 'object'
    and jsonb_typeof(knowledge -> 'types') = 'object'
  ));
