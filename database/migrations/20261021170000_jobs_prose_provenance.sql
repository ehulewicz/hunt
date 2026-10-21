-- Prose is saved at the end of every run, not only one that went in, as one
-- text whose spans say where they came from: drafted by a model, or taken from
-- a previous answer. Text in neither is yours and has no span. The worker
-- computes the spans when it saves, by diffing the final text against what it
-- started from; the page only draws them. `submitted` says whether the
-- application it was written for went in.

alter table app.jobs_prose
  add column provenance jsonb
    check (provenance is null or jsonb_typeof(provenance) = 'array'),
  add column submitted boolean not null default false;

-- Until now prose was saved from an application only once it went in.
update app.jobs_prose set submitted = true where application_id is not null;
