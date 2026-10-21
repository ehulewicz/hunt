-- What work-history forms ask of an entry, beyond what the resume shows.
-- City, region (a state or province as written, "GA"), and country (a name,
-- as the address facts store one) replace the single location string, which
-- the API now renders from them and still writes until a later migration
-- drops it. `job_title` and `employer` are what a form gets when the heading
-- is not one: a research heading names the lab, not the role. Null means the
-- form gets `title` and `org`.

alter table app.jobs_resume_entries
  add column city text check (city is null or length(trim(city)) > 0),
  add column region text check (region is null or length(trim(region)) > 0),
  add column country text check (country is null or length(trim(country)) > 0),
  add column job_title text check (job_title is null or length(trim(job_title)) > 0),
  add column employer text check (employer is null or length(trim(employer)) > 0);

-- "City, ST" is a US city and its state; otherwise the last part is the
-- country and a middle part, if any, the region.
with parts as (
  select id, array(select trim(part) from unnest(string_to_array(location, ',')) part) as part
  from app.jobs_resume_entries
  where location is not null and trim(location) <> ''
)
update app.jobs_resume_entries entry
set
  city = nullif(parts.part[1], ''),
  region = case
    when cardinality(parts.part) = 2 and parts.part[2] ~ '^[A-Z]{2}$' then parts.part[2]
    when cardinality(parts.part) >= 3 then nullif(parts.part[2], '')
  end,
  country = case
    when cardinality(parts.part) = 2 and parts.part[2] ~ '^[A-Z]{2}$' then 'United States'
    when cardinality(parts.part) >= 2 then nullif(parts.part[cardinality(parts.part)], '')
  end
from parts
where entry.id = parts.id;

-- Research is work history on forms: the role is the researcher, and the
-- employer the lab as the heading names it.
update app.jobs_resume_entries
set job_title = 'Undergraduate Researcher', employer = title
where section = 'research';
