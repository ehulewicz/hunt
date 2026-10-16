-- Jobs holds no fact that is hidden from Jobs. The `sensitive` flag only ever
-- masked a value in the worker's own terminal and withheld it from the worker's
-- own `claude -p` calls, which made a fill harder to read back and a failure
-- harder to diagnose. The product is single-user and the column has no other
-- reader, so it goes.
alter table app.jobs_facts drop column if exists sensitive;
