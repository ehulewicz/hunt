-- Registry instrumentation: every fill attempt becomes a measurement.
--
-- A control is a widget kind plus its structural attributes, never the ATS or
-- the question, so the same widget on two companies is one row. A strategy is a
-- named way of operating a control kind. A fill event is one attempt, written
-- during the run; every success rate is a query over these rows.
--
-- Until the answers slice adds jobs_questions and jobs_patterns, a fill event
-- names its question by normalized key and its pattern by resolver id; that
-- slice adds question_id and pattern_id and backfills them from these columns.

create table app.jobs_controls (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in (
    'text_entry', 'native_select', 'choice_group', 'multi_choice_group',
    'toggle', 'dynamic_choice', 'file'
  )),
  attributes jsonb not null check (jsonb_typeof(attributes) = 'object'),
  first_seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (user_id, kind, attributes)
);

create table app.jobs_strategies (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null check (name ~ '^[a-z][a-z0-9_]*$'),
  control_kind text not null check (control_kind in (
    'text_entry', 'native_select', 'choice_group', 'multi_choice_group',
    'toggle', 'dynamic_choice', 'file'
  )),
  precedence integer not null check (precedence >= 0),
  status text not null default 'active' check (status in (
    'candidate', 'repaired', 'validated', 'active', 'rejected', 'retired'
  )),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, name, control_kind)
);

create trigger jobs_strategies_set_updated_at
before update on app.jobs_strategies
for each row execute function app.set_updated_at();

create table app.jobs_fill_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  run_id uuid not null references app.jobs_autofill_runs(id) on delete cascade,
  step integer not null check (step >= 0),
  question_key text not null,
  resolver_id text,
  fact_key text,
  formatter text,
  source text,
  control_id uuid not null references app.jobs_controls(id),
  strategy_id uuid references app.jobs_strategies(id),
  ats text not null,
  value_hash text,
  -- null when the fill went through or was never attempted; operated_ok tells
  -- the two apart (true = read back, null = not attempted).
  stopped_at text check (stopped_at in ('pattern', 'format', 'strategy')),
  operated_ok boolean,
  created_at timestamptz not null default now(),
  check (stopped_at is null or operated_ok is not true)
);

create index jobs_fill_events_control_idx
on app.jobs_fill_events (user_id, control_id, resolver_id);

create index jobs_fill_events_run_idx
on app.jobs_fill_events (run_id, step);

grant select, insert, update, delete on
  app.jobs_controls,
  app.jobs_strategies,
  app.jobs_fill_events
to personal_os_api;

-- The ten strategies that exist in code, active from the start (R27), in
-- candidate order within each kind.
insert into app.jobs_strategies (user_id, name, control_kind, precedence)
select users.user_id, strategy.name, strategy.control_kind, strategy.precedence
from (select distinct user_id from app.jobs_profile) users
cross join (
  values
    ('upload_file', 'file', 0),
    ('select_by_label', 'native_select', 0),
    ('select_by_value', 'native_select', 1),
    ('select_multiple', 'multi_choice_group', 0),
    ('check_control', 'multi_choice_group', 1),
    ('uncheck_control', 'multi_choice_group', 2),
    ('combobox_choose_from_menu', 'dynamic_choice', 0),
    ('combobox_type_and_select', 'dynamic_choice', 1),
    ('click_option_button', 'choice_group', 0),
    ('check_control', 'choice_group', 1),
    ('check_control', 'toggle', 0),
    ('uncheck_control', 'toggle', 1),
    ('set_value', 'text_entry', 0)
) as strategy(name, control_kind, precedence);
