-- =============================================================================
-- Tora — authentication, subscription state and usage metering
-- Migration 0001
--
-- DESIGN RULE: THE CLIENT IS NOT TRUSTED.
-- Tora is a desktop app. The user controls the machine, the Python source, the
-- local files and the network, so nothing the client says about itself can be
-- believed. This schema is built so that a user who patches the app still
-- cannot:
--     * give themselves a paid plan      (no client write path to profiles.plan)
--     * forge or inflate usage           (kind + quantity validated server-side)
--     * backdate usage                   (created_at is not a parameter)
--     * reset a quota by deleting rows   (no DELETE policy exists)
--     * read or write another user's data (every policy pins auth.uid())
--
-- Plan changes come from the service_role key only — which belongs on a server
-- you control (a billing webhook), NEVER inside the shipped application.
--
-- Idempotent: safe to run more than once. Creates nothing destructive.
-- =============================================================================

begin;

-- ── Plans ────────────────────────────────────────────────────────────────────
-- The price list lives in the database, not the client, so quotas can be
-- changed without shipping a new build. `limits` is jsonb so new usage
-- categories can be added later without a migration. A JSON null means
-- "unlimited".
create table if not exists public.plans (
    key         text primary key,
    name        text        not null,
    limits      jsonb       not null default '{}'::jsonb,
    sort_order  int         not null default 0,
    is_public   boolean     not null default true,
    created_at  timestamptz not null default now()
);

insert into public.plans (key, name, limits, sort_order) values
    ('free', 'Free',
     '{"chat": 50, "agent": 5, "vision": 50, "search": 50}'::jsonb, 0),
    ('pro',  'Pro',
     '{"chat": null, "agent": null, "vision": null, "search": null}'::jsonb, 1)
on conflict (key) do nothing;


-- ── Profiles ─────────────────────────────────────────────────────────────────
-- One row per auth user, created automatically by the trigger below so that a
-- signup completed on the website needs no cooperation from the client.
create table if not exists public.profiles (
    id              uuid primary key references auth.users(id) on delete cascade,
    email           text,
    display_name    text,
    plan            text        not null default 'free' references public.plans(key),
    plan_status     text        not null default 'active'
                    check (plan_status in ('active','trialing','past_due','canceled')),
    plan_renews_at  timestamptz,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);


-- ── Usage events ─────────────────────────────────────────────────────────────
-- Append-only. The client has no INSERT policy; rows arrive solely through
-- record_usage() below, which stamps user_id from the JWT and created_at from
-- the server clock.
create table if not exists public.usage_events (
    id          bigint generated always as identity primary key,
    user_id     uuid        not null references auth.users(id) on delete cascade,
    kind        text        not null
                check (kind in ('chat','agent','vision','search','point')),
    quantity    int         not null default 1 check (quantity between 1 and 100),
    meta        jsonb       not null default '{}'::jsonb,
    created_at  timestamptz not null default now()
);

create index if not exists usage_events_user_created_idx
    on public.usage_events (user_id, created_at desc);
create index if not exists usage_events_user_kind_created_idx
    on public.usage_events (user_id, kind, created_at desc);


-- ── updated_at ───────────────────────────────────────────────────────────────
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $fn$
begin
    new.updated_at := now();
    return new;
end;
$fn$;

drop trigger if exists profiles_touch_updated_at on public.profiles;
create trigger profiles_touch_updated_at
    before update on public.profiles
    for each row execute function public.touch_updated_at();


-- ── Auto-create a profile on signup ──────────────────────────────────────────
-- search_path is pinned on every SECURITY DEFINER function here. Leaving it
-- unpinned is a privilege-escalation vector: a caller could put a malicious
-- function earlier on the path and have it run as the definer.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
begin
    insert into public.profiles (id, email)
    values (new.id, new.email)
    on conflict (id) do nothing;
    return new;
end;
$fn$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- Backfill anyone who signed up before this migration ran.
insert into public.profiles (id, email)
select u.id, u.email from auth.users u
on conflict (id) do nothing;


-- ── record_usage() ───────────────────────────────────────────────────────────
-- The ONLY write path into usage_events. Everything a malicious client might
-- lie about is either taken from the JWT or clamped here.
create or replace function public.record_usage(
    p_kind     text,
    p_quantity int   default 1,
    p_meta     jsonb default '{}'::jsonb
)
returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid uuid := auth.uid();
    v_id  bigint;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;

    if p_kind is null
       or p_kind not in ('chat','agent','vision','search','point') then
        raise exception 'unknown usage kind: %', p_kind using errcode = '22023';
    end if;

    -- Clamp rather than trust: a patched client can neither inflate a count
    -- nor write a zero/negative one to hide activity.
    p_quantity := least(greatest(coalesce(p_quantity, 1), 1), 100);

    -- user_id comes from the JWT and created_at from the server clock, so
    -- neither can be spoofed or backdated by the caller.
    insert into public.usage_events (user_id, kind, quantity, meta)
    values (v_uid, p_kind, p_quantity, coalesce(p_meta, '{}'::jsonb))
    returning id into v_id;

    return v_id;
end;
$fn$;


-- ── get_usage_summary() ──────────────────────────────────────────────────────
-- One round trip for everything the tray's Usage view needs: plan, period and
-- per-category totals. Self-heals a missing profile so a user can never be
-- locked out by a failed trigger.
create or replace function public.get_usage_summary()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid    uuid := auth.uid();
    v_start  timestamptz := (date_trunc('month', now() at time zone 'UTC'))
                            at time zone 'UTC';
    v_prof   public.profiles%rowtype;
    v_limits jsonb;
    v_counts jsonb;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;

    select * into v_prof from public.profiles where id = v_uid;
    if not found then
        insert into public.profiles (id, email)
        values (v_uid, (select email from auth.users where id = v_uid))
        on conflict (id) do nothing;
        select * into v_prof from public.profiles where id = v_uid;
    end if;

    select coalesce(p.limits, '{}'::jsonb) into v_limits
      from public.plans p where p.key = v_prof.plan;

    select coalesce(jsonb_object_agg(s.kind, s.total), '{}'::jsonb)
      into v_counts
      from (
            select e.kind, sum(e.quantity)::int as total
              from public.usage_events e
             where e.user_id = v_uid
               and e.created_at >= v_start
             group by e.kind
           ) s;

    return jsonb_build_object(
        'user_id',        v_uid,
        'email',          v_prof.email,
        'display_name',   v_prof.display_name,
        'plan',           v_prof.plan,
        'plan_status',    v_prof.plan_status,
        'plan_renews_at', v_prof.plan_renews_at,
        'period_start',   v_start,
        'period_end',     v_start + interval '1 month',
        'limits',         coalesce(v_limits, '{}'::jsonb),
        'usage',          v_counts
    );
end;
$fn$;


-- ── Row Level Security ───────────────────────────────────────────────────────
alter table public.plans        enable row level security;
alter table public.profiles     enable row level security;
alter table public.usage_events enable row level security;

-- Deliberately NO insert/update/delete policies below. Under RLS an operation
-- with no permitting policy is denied, so this is what blocks a client from
-- promoting its own plan or wiping its usage to reset a quota.

drop policy if exists plans_select_public on public.plans;
create policy plans_select_public on public.plans
    for select to authenticated
    using (is_public);

drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
    for select to authenticated
    using (id = auth.uid());

drop policy if exists usage_select_own on public.usage_events;
create policy usage_select_own on public.usage_events
    for select to authenticated
    using (user_id = auth.uid());


-- ── Grants ───────────────────────────────────────────────────────────────────
-- RLS filters rows; grants decide which verbs exist at all. Both are needed.
revoke all on public.plans        from anon, authenticated;
revoke all on public.profiles     from anon, authenticated;
revoke all on public.usage_events from anon, authenticated;

grant select on public.plans        to authenticated;
grant select on public.profiles     to authenticated;
grant select on public.usage_events to authenticated;

revoke all on function public.record_usage(text, int, jsonb) from public, anon;
revoke all on function public.get_usage_summary()            from public, anon;

grant execute on function public.record_usage(text, int, jsonb) to authenticated;
grant execute on function public.get_usage_summary()            to authenticated;

commit;
