-- =============================================================================
-- HeyTora — metering that holds against a modified app
-- Migration 0004  (needs 0001, 0003)
--
-- 0003 counted what the app SAID it was doing. A copy of the app is easy to
-- change, so here the server decides:
--
--   * A talk message is a talk WINDOW the server opens (heytora_window):
--     talk_minutes long (Free 10, Pro/Max 30). Everything Tora does for that
--     question - the voice session, looking at the screen, planning a
--     walkthrough - runs on the window's ticket and stops when it ends.
--     A window allows passes_per_talk voice passes (a reconnect, a quick
--     follow-up), each ending with the window (heytora_pass).
--   * An agent message is an agent window (agent_minutes, 30).
--   * A dictation is one pass, dictation_minutes at most.
--   * Every forwarded model call needs a valid window ticket, and its real
--     cost - from Google's own token counts - is recorded (usage_costs).
--     Each plan has a hidden monthly ceiling on that cost (budget_usd), so
--     no plan can be used to drain the business key, whatever is sent.
--   * Unlimited plans have hidden daily fair-use ceilings (<kind>_day).
--
-- All of these are in plans.limits: change the numbers without a release.
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

-- ── The plans' knobs ─────────────────────────────────────────────────────────
-- Shown to users: talk, dictation, agent. The rest are the server's own.
update public.plans set limits = limits || '{
    "budget_usd": 2,  "talk_day": 25,  "dictation_day": 100,  "agent_day": 10,
    "talk_minutes": 10, "agent_minutes": 30, "dictation_minutes": 10, "passes_per_talk": 3
}'::jsonb where key = 'free';
update public.plans set limits = limits || '{
    "budget_usd": 12, "talk_day": 300, "dictation_day": 500,  "agent_day": 50,
    "talk_minutes": 30, "agent_minutes": 30, "dictation_minutes": 30, "passes_per_talk": 6
}'::jsonb where key = 'pro';
update public.plans set limits = limits || '{
    "budget_usd": 60, "talk_day": 600, "dictation_day": 1000, "agent_day": 200,
    "talk_minutes": 30, "agent_minutes": 30, "dictation_minutes": 30, "passes_per_talk": 6
}'::jsonb where key = 'max';


-- ── A voice pass inside a talk window is recorded as a "pass" ────────────────
alter table public.usage_events drop constraint if exists usage_events_kind_check;
alter table public.usage_events add constraint usage_events_kind_check
    check (kind in ('chat','agent','vision','search','point','talk','dictation','calls','pass'));


-- ── What each forwarded call cost ────────────────────────────────────────────
-- Written only by the server function (service role, heytora_record_cost);
-- a user can read their own rows and nothing else.
create table if not exists public.usage_costs (
    id             bigint generated always as identity primary key,
    user_id        uuid        not null references auth.users(id) on delete cascade,
    kind           text        not null default '',
    model          text        not null default '',
    input_tokens   bigint      not null default 0 check (input_tokens  >= 0),
    output_tokens  bigint      not null default 0 check (output_tokens >= 0),
    cost_micro     bigint      not null default 0 check (cost_micro    >= 0),   -- millionths of a US dollar
    created_at     timestamptz not null default now()
);
create index if not exists usage_costs_user_created_idx
    on public.usage_costs (user_id, created_at desc);

alter table public.usage_costs enable row level security;
drop policy if exists usage_costs_select_own on public.usage_costs;
create policy usage_costs_select_own on public.usage_costs
    for select to authenticated using (user_id = auth.uid());
revoke all on public.usage_costs from anon, authenticated;
grant select on public.usage_costs to authenticated;


-- ── The plan that applies: a lapsed or cancelled one is Free ─────────────────
create or replace function public._heytora_plan(p_uid uuid)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_plan   text;
    v_status text;
begin
    select p.plan, p.plan_status into v_plan, v_status from public.profiles p where p.id = p_uid;
    if not found then
        insert into public.profiles (id, email)
        values (p_uid, (select u.email from auth.users u where u.id = p_uid))
        on conflict (id) do nothing;
        v_plan := 'free';
        v_status := 'active';
    end if;
    if v_status not in ('active', 'trialing') then
        v_plan := 'free';
    end if;
    return jsonb_build_object(
        'plan', v_plan,
        'limits', coalesce((select pl.limits from public.plans pl where pl.key = v_plan), '{}'::jsonb));
end;
$fn$;


-- ── Spend one unit, if the plan allows it ─────────────────────────────────────
-- The one place every check lives: in the plan at all, the per-minute brake,
-- the daily fair-use ceiling, the monthly allowance, and - for forwarded
-- calls - the monthly cost ceiling. Atomic per user and kind.
create or replace function public._heytora_spend(
    p_uid uuid, p_kind text, p_quantity int, p_meta jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_month   timestamptz := (date_trunc('month', now() at time zone 'UTC')) at time zone 'UTC';
    v_day     timestamptz := (date_trunc('day',   now() at time zone 'UTC')) at time zone 'UTC';
    v_p       jsonb;
    v_plan    text;
    v_limits  jsonb;
    v_limit   jsonb;
    v_cap     jsonb;
    v_used    int;
    v_today   int;
    v_recent  int;
    v_per_min int;
    v_spent   bigint;
    v_id      bigint;
begin
    p_quantity := least(greatest(coalesce(p_quantity, 1), 1), 100);
    perform pg_advisory_xact_lock(hashtext(p_uid::text || ':' || p_kind));

    v_p := public._heytora_plan(p_uid);
    v_plan := v_p ->> 'plan';
    v_limits := v_p -> 'limits';
    if not (v_limits ? p_kind) then
        return jsonb_build_object('ok', false, 'reason', 'not_in_plan', 'kind', p_kind, 'plan', v_plan);
    end if;
    v_limit := v_limits -> p_kind;

    -- A brake on bursts, on every plan: a stuck or modified app cannot burn
    -- a month in a minute.
    v_per_min := case p_kind when 'calls' then 120 when 'talk' then 20
                             when 'dictation' then 30 else 10 end;
    select count(*) into v_recent from public.usage_events e
     where e.user_id = p_uid and e.kind = p_kind and e.created_at > now() - interval '1 minute';
    if v_recent >= v_per_min then
        return jsonb_build_object('ok', false, 'reason', 'rate', 'kind', p_kind, 'plan', v_plan);
    end if;

    -- Fair use per day (UTC), on every plan that has a ceiling for this kind.
    v_cap := v_limits -> (p_kind || '_day');
    if jsonb_typeof(v_cap) = 'number' then
        select coalesce(sum(e.quantity), 0)::int into v_today from public.usage_events e
         where e.user_id = p_uid and e.kind = p_kind and e.created_at >= v_day;
        if v_today + p_quantity > (v_cap #>> '{}')::int then
            return jsonb_build_object('ok', false, 'reason', 'day', 'kind', p_kind, 'plan', v_plan,
                                      'limit', (v_cap #>> '{}')::int);
        end if;
    end if;

    -- The month's allowance.
    select coalesce(sum(e.quantity), 0)::int into v_used from public.usage_events e
     where e.user_id = p_uid and e.kind = p_kind and e.created_at >= v_month;
    if jsonb_typeof(v_limit) = 'number' and v_used + p_quantity > (v_limit #>> '{}')::int then
        return jsonb_build_object('ok', false, 'reason', 'limit', 'kind', p_kind, 'used', v_used,
                                  'limit', (v_limit #>> '{}')::int, 'plan', v_plan);
    end if;

    -- What the month's forwarded calls really cost, against the plan's ceiling.
    if p_kind = 'calls' and jsonb_typeof(v_limits -> 'budget_usd') = 'number' then
        select coalesce(sum(c.cost_micro), 0) into v_spent from public.usage_costs c
         where c.user_id = p_uid and c.created_at >= v_month;
        if v_spent >= ((v_limits ->> 'budget_usd')::numeric * 1000000)::bigint then
            return jsonb_build_object('ok', false, 'reason', 'budget', 'kind', p_kind, 'plan', v_plan);
        end if;
    end if;

    insert into public.usage_events (user_id, kind, quantity, meta)
    values (p_uid, p_kind, p_quantity, coalesce(p_meta, '{}'::jsonb))
    returning id into v_id;

    return jsonb_build_object(
        'ok', true, 'id', v_id, 'kind', p_kind, 'used', v_used + p_quantity, 'plan', v_plan,
        'limit', case when jsonb_typeof(v_limit) = 'number' then (v_limit #>> '{}')::int end,
        'limits', v_limits);
end;
$fn$;


-- ── heytora_consume(): dictation passes and forwarded calls ──────────────────
create or replace function public.heytora_consume(
    p_kind     text,
    p_quantity int   default 1,
    p_meta     jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid uuid := auth.uid();
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    if p_kind not in ('talk', 'dictation', 'agent', 'calls') then
        raise exception 'unknown kind: %', p_kind using errcode = '22023';
    end if;
    return public._heytora_spend(v_uid, p_kind, p_quantity, p_meta);
end;
$fn$;


-- ── heytora_window(): a talk message or an agent message ──────────────────────
-- p_new: the app starts a new question (a new talk message). Otherwise the
-- window still open is handed back, at no charge, with its own end - a
-- window never grows, however often it is asked for.
create or replace function public.heytora_window(
    p_kind text,
    p_new  boolean default false,
    p_meta jsonb   default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid     uuid := auth.uid();
    v_p       jsonb;
    v_minutes int;
    v_id      bigint;
    v_start   timestamptz;
    v_res     jsonb;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    if p_kind not in ('talk', 'agent') then
        raise exception 'unknown window kind: %', p_kind using errcode = '22023';
    end if;
    perform pg_advisory_xact_lock(hashtext(v_uid::text || ':window:' || p_kind));

    v_p := public._heytora_plan(v_uid);
    v_minutes := least(greatest(coalesce((v_p -> 'limits' ->> (p_kind || '_minutes'))::int, 30), 1), 120);

    if not coalesce(p_new, false) then
        select e.id, e.created_at into v_id, v_start from public.usage_events e
         where e.user_id = v_uid and e.kind = p_kind
           and e.created_at > now() - make_interval(mins => v_minutes)
         order by e.created_at desc
         limit 1;
        if found then
            return jsonb_build_object(
                'ok', true, 'kind', p_kind, 'window', v_id, 'plan', v_p ->> 'plan', 'reused', true,
                'expires_at', extract(epoch from v_start + make_interval(mins => v_minutes)));
        end if;
    end if;

    v_res := public._heytora_spend(v_uid, p_kind, 1, p_meta);
    if not (v_res ->> 'ok')::boolean then
        return v_res;
    end if;
    return (v_res - 'limits') || jsonb_build_object(
        'window', v_res -> 'id', 'reused', false,
        'expires_at', extract(epoch from now() + make_interval(mins => v_minutes)));
end;
$fn$;


-- ── heytora_pass(): a voice pass inside a talk window ─────────────────────────
-- At most passes_per_talk per window (a reconnect, a quick follow-up), and
-- each one ends when the window does - Google closes the session then.
create or replace function public.heytora_pass(p_window bigint, p_model text default '')
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid     uuid := auth.uid();
    v_start   timestamptz;
    v_p       jsonb;
    v_minutes int;
    v_cap     int;
    v_n       int;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    perform pg_advisory_xact_lock(hashtext(v_uid::text || ':pass'));

    select e.created_at into v_start from public.usage_events e
     where e.id = p_window and e.user_id = v_uid and e.kind = 'talk';
    if not found then
        return jsonb_build_object('ok', false, 'reason', 'window', 'kind', 'talk');
    end if;
    v_p := public._heytora_plan(v_uid);
    v_minutes := least(greatest(coalesce((v_p -> 'limits' ->> 'talk_minutes')::int, 30), 1), 120);
    -- A pass needs a minute to be worth opening; a window that is nearly
    -- over is not stretched for one - the app starts the next instead.
    if v_start + make_interval(mins => v_minutes) <= now() + interval '1 minute' then
        return jsonb_build_object('ok', false, 'reason', 'window', 'kind', 'talk');
    end if;

    v_cap := coalesce((v_p -> 'limits' ->> 'passes_per_talk')::int, 3);
    select count(*) into v_n from public.usage_events e
     where e.user_id = v_uid and e.kind = 'pass' and e.meta ->> 'window' = p_window::text;
    if v_n >= v_cap then
        return jsonb_build_object('ok', false, 'reason', 'passes', 'kind', 'talk', 'plan', v_p ->> 'plan');
    end if;

    insert into public.usage_events (user_id, kind, quantity, meta)
    values (v_uid, 'pass', 1, jsonb_build_object('window', p_window, 'model', left(coalesce(p_model, ''), 80)));
    return jsonb_build_object(
        'ok', true, 'window', p_window, 'plan', v_p ->> 'plan',
        'expires_at', extract(epoch from v_start + make_interval(mins => v_minutes)));
end;
$fn$;


-- ── heytora_record_cost(): the server function's note of what a call cost ────
create or replace function public.heytora_record_cost(
    p_user  uuid,
    p_kind  text,
    p_model text,
    p_in    bigint,
    p_out   bigint,
    p_micro bigint
)
returns void
language sql
security definer
set search_path = public, pg_temp
as $fn$
    insert into public.usage_costs (user_id, kind, model, input_tokens, output_tokens, cost_micro)
    values (p_user, left(coalesce(p_kind, ''), 20), left(coalesce(p_model, ''), 80),
            greatest(coalesce(p_in, 0), 0), greatest(coalesce(p_out, 0), 0),
            greatest(coalesce(p_micro, 0), 0));
$fn$;


-- ── get_usage_summary(): what the app and the website show ───────────────────
-- Only the allowances on the price list; the server's own ceilings stay out.
create or replace function public.get_usage_summary()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid    uuid := auth.uid();
    v_start  timestamptz := (date_trunc('month', now() at time zone 'UTC')) at time zone 'UTC';
    v_prof   public.profiles%rowtype;
    v_p      jsonb;
    v_shown  text[] := array['talk', 'dictation', 'agent'];
    v_limits jsonb := '{}'::jsonb;
    v_counts jsonb;
    k        text;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    v_p := public._heytora_plan(v_uid);
    select * into v_prof from public.profiles where id = v_uid;

    foreach k in array v_shown loop
        if (v_p -> 'limits') ? k then
            v_limits := v_limits || jsonb_build_object(k, v_p -> 'limits' -> k);
        end if;
    end loop;

    select coalesce(jsonb_object_agg(s.kind, s.total), '{}'::jsonb) into v_counts
      from (select e.kind, sum(e.quantity)::int as total
              from public.usage_events e
             where e.user_id = v_uid and e.created_at >= v_start and e.kind = any(v_shown)
             group by e.kind) s;

    return jsonb_build_object(
        'user_id',        v_uid,
        'email',          v_prof.email,
        'display_name',   v_prof.display_name,
        'plan',           v_p ->> 'plan',            -- what applies now (lapsed = free)
        'plan_chosen',    v_prof.plan,
        'plan_status',    v_prof.plan_status,
        'plan_renews_at', v_prof.plan_renews_at,
        'period_start',   v_start,
        'period_end',     v_start + interval '1 month',
        'limits',         v_limits,
        'usage',          v_counts
    );
end;
$fn$;


-- ── Who may call what ─────────────────────────────────────────────────────────
-- Supabase grants new functions to anon and authenticated by default: take
-- that back everywhere, then give each caller exactly what it needs.
revoke all on function public._heytora_plan(uuid)                         from public, anon, authenticated;
revoke all on function public._heytora_spend(uuid, text, int, jsonb)      from public, anon, authenticated;
revoke all on function public.heytora_consume(text, int, jsonb)           from public, anon;
revoke all on function public.heytora_window(text, boolean, jsonb)        from public, anon;
revoke all on function public.heytora_pass(bigint, text)                  from public, anon;
revoke all on function public.heytora_record_cost(uuid, text, text, bigint, bigint, bigint)
                                                                          from public, anon, authenticated;
revoke all on function public.get_usage_summary()                         from public, anon;
-- record_usage() (0001) let a client add usage rows; nothing uses it.
revoke all on function public.record_usage(text, int, jsonb)              from public, anon, authenticated;

grant execute on function public.heytora_consume(text, int, jsonb)    to authenticated;
grant execute on function public.heytora_window(text, boolean, jsonb) to authenticated;
grant execute on function public.heytora_pass(bigint, text)           to authenticated;
grant execute on function public.get_usage_summary()                  to authenticated;
grant execute on function public.heytora_record_cost(uuid, text, text, bigint, bigint, bigint) to service_role;

commit;
