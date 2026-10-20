-- Devices: every machine that runs the Hunt agent lists itself here. The agent
-- sends a heartbeat every 30 seconds through its local API, so this row says
-- whether the device is up, whether `os doctor` passes there, which commit it
-- runs, and which run it is busy with. The id is generated once on the device
-- and kept in ~/.config/personal-os/device.json; the user is always the
-- verified JWT's. Nothing here carries the stream: the page reads this list,
-- then connects to the device over the tailnet.

create table app.jobs_devices (
  id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (length(trim(name)) > 0),
  url text not null check (url ~ '^https://'),
  commit text not null check (commit ~ '^[0-9a-f]{7,64}$'),
  local_changes boolean not null,
  ready boolean not null,
  problem text,
  active_run_id uuid references app.jobs_autofill_runs(id) on delete set null,
  last_seen_at timestamptz not null default now(),
  -- A device that is not ready says why: the first failing doctor check.
  constraint jobs_devices_problem_when_not_ready check ((problem is null) = ready)
);

create index jobs_devices_user_idx on app.jobs_devices (user_id, name);

grant select, insert, update on app.jobs_devices to personal_os_api;
