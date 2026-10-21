-- One pinned model: text-embedding-3-small, 1536 dimensions. Changing it
-- requires replacing every cached vector before comparisons resume.
create extension if not exists vector with schema extensions;
alter table app.jobs_questions add column embedding extensions.vector(1536);
grant usage on schema extensions to personal_os_api;
