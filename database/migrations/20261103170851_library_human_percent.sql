-- Each narrative-library entry's GPTZero human share. Only a scan sets it;
-- any change to the entry's answer clears it, so a stale score never shows.

alter table app.jobs_library_entries
  add column human_percent smallint
    check (human_percent is null or human_percent between 0 and 100);

comment on column app.jobs_library_entries.human_percent is
  'GPTZero''s share (0 to 100) that the answer reads as human; null when unchecked or when the answer changed since the check';
