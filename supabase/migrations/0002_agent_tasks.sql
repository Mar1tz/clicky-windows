-- =============================================================================
-- Tora — optional cloud mirror of the local agent history
-- Migration 0002
--
-- The source of truth for agent tasks is the local SQLite file
-- (agents/persistence/sqlite_store.py). These tables let a signed-in user
-- mirror that history so it survives a reinstall and is visible from another
-- device. The client owns its own rows and nothing else:
--     * every row is pinned to auth.uid() on insert (default) and by RLS
--     * a user can read, insert, update and delete only their own rows
--     * nothing here feeds billing or quotas — usage stays in migration 0001,
--       where the client has no write path
--
-- Columns mirror the SQLite schema 1:1 so the client can upsert the same
-- dicts it already writes locally. Timestamps are epoch seconds (double
-- precision) exactly as the app stores them; `synced_at` is the only value
-- the server stamps. Ids are the app's 12-hex ids, unique per user only, so
-- every primary key is (user_id, id).
--
-- Idempotent: safe to run more than once. Creates nothing destructive.
-- =============================================================================

begin;

-- ── Tasks ────────────────────────────────────────────────────────────────────
create table if not exists public.agent_tasks (
    user_id     uuid        not null default auth.uid()
                            references auth.users(id) on delete cascade,
    id          text        not null,
    status      text        not null
                check (status in ('pending','planning','waiting_approval','running',
                                  'paused','verifying','completed','failed','cancelled')),
    kind        text        not null default 'general',
    title       text        not null default '',
    created_at  double precision not null,
    updated_at  double precision not null,
    json        jsonb       not null default '{}'::jsonb,
    synced_at   timestamptz not null default now(),
    primary key (user_id, id)
);

create index if not exists agent_tasks_user_created_idx
    on public.agent_tasks (user_id, created_at desc);
create index if not exists agent_tasks_user_status_idx
    on public.agent_tasks (user_id, status, created_at desc);


-- ── Plans ────────────────────────────────────────────────────────────────────
-- No FK to agent_tasks: a replan may be mirrored before its task row lands.
create table if not exists public.agent_plans (
    user_id     uuid        not null default auth.uid()
                            references auth.users(id) on delete cascade,
    id          text        not null,
    task_id     text        not null,
    json        jsonb       not null default '{}'::jsonb,
    synced_at   timestamptz not null default now(),
    primary key (user_id, id)
);

create index if not exists agent_plans_user_task_idx
    on public.agent_plans (user_id, task_id);


-- ── Events ───────────────────────────────────────────────────────────────────
-- Append-mostly; the timeline UI reads (user_id, task_id) ordered by ts.
create table if not exists public.agent_events (
    user_id     uuid        not null default auth.uid()
                            references auth.users(id) on delete cascade,
    id          text        not null,
    task_id     text,
    agent_id    text,
    kind        text        not null,
    level       text        not null default 'info'
                check (level in ('info','warn','error')),
    ts          double precision not null,
    json        jsonb       not null default '{}'::jsonb,
    synced_at   timestamptz not null default now(),
    primary key (user_id, id)
);

create index if not exists agent_events_user_task_ts_idx
    on public.agent_events (user_id, task_id, ts);


-- ── Agents ───────────────────────────────────────────────────────────────────
-- Last known AgentState per agent (spec + status), one row per agent id.
create table if not exists public.agent_states (
    user_id     uuid        not null default auth.uid()
                            references auth.users(id) on delete cascade,
    id          text        not null,
    json        jsonb       not null default '{}'::jsonb,
    synced_at   timestamptz not null default now(),
    primary key (user_id, id)
);


-- ── synced_at ────────────────────────────────────────────────────────────────
-- Re-stamped on every update so a device can pull "everything since I last
-- synced" without trusting client clocks (created_at / ts / updated_at are
-- client values and only meaningful relative to each other).
create or replace function public.touch_synced_at()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $fn$
begin
    new.synced_at := now();
    return new;
end;
$fn$;

drop trigger if exists agent_tasks_touch_synced_at on public.agent_tasks;
create trigger agent_tasks_touch_synced_at
    before update on public.agent_tasks
    for each row execute function public.touch_synced_at();

drop trigger if exists agent_plans_touch_synced_at on public.agent_plans;
create trigger agent_plans_touch_synced_at
    before update on public.agent_plans
    for each row execute function public.touch_synced_at();

drop trigger if exists agent_events_touch_synced_at on public.agent_events;
create trigger agent_events_touch_synced_at
    before update on public.agent_events
    for each row execute function public.touch_synced_at();

drop trigger if exists agent_states_touch_synced_at on public.agent_states;
create trigger agent_states_touch_synced_at
    before update on public.agent_states
    for each row execute function public.touch_synced_at();


-- ── Row Level Security ───────────────────────────────────────────────────────
-- "Own rows only" in every direction. `with check` on insert/update stops a
-- patched client from writing a row under someone else's user_id even if it
-- sends one explicitly instead of relying on the column default.
alter table public.agent_tasks  enable row level security;
alter table public.agent_plans  enable row level security;
alter table public.agent_events enable row level security;
alter table public.agent_states enable row level security;

drop policy if exists agent_tasks_own on public.agent_tasks;
create policy agent_tasks_own on public.agent_tasks
    for all to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

drop policy if exists agent_plans_own on public.agent_plans;
create policy agent_plans_own on public.agent_plans
    for all to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

drop policy if exists agent_events_own on public.agent_events;
create policy agent_events_own on public.agent_events
    for all to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());

drop policy if exists agent_states_own on public.agent_states;
create policy agent_states_own on public.agent_states
    for all to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());


-- ── Grants ───────────────────────────────────────────────────────────────────
-- RLS filters rows; grants decide which verbs exist at all. Both are needed.
revoke all on public.agent_tasks  from anon, authenticated;
revoke all on public.agent_plans  from anon, authenticated;
revoke all on public.agent_events from anon, authenticated;
revoke all on public.agent_states from anon, authenticated;

grant select, insert, update, delete on public.agent_tasks  to authenticated;
grant select, insert, update, delete on public.agent_plans  to authenticated;
grant select, insert, update, delete on public.agent_events to authenticated;
grant select, insert, update, delete on public.agent_states to authenticated;

commit;
