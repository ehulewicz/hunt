-- The options a control offered when it was filled: a select's options, a
-- choice group's labels, a typeahead's menu once read. A learned pattern or
-- formatter is replayed offline against them, so a sighting keeps what the
-- page actually offered. Null when the control offered none, or its menu was
-- never read.

alter table app.jobs_fill_events
  add column options jsonb
    check (options is null or jsonb_typeof(options) = 'array');
