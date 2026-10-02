-- =============================================================================
-- HeyTora — the wake word is not a dictation
-- Migration 0006  (needs 0004, 0005)
--
-- With "say Tora" on, every bit of speech near the PC is checked for the
-- wake word by the dictation model: one voice pass per check. They were
-- counted as dictations - 403 of them in a night on the owner's PC - so the
-- dictation count meant nothing, and on Free the wake word alone would have
-- spent the day's dictations in minutes.
--
-- Now a check is its own kind, "wake": not on the price list and not in
-- the allowances users see, one minute per pass, a per-minute brake, and a
-- hidden daily ceiling. The business's report shows them in their own
-- column.
--
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

update public.plans set limits = limits || '{"wake": null, "wake_day": 500,  "wake_minutes": 1}'::jsonb
 where key = 'free';
update public.plans set limits = limits || '{"wake": null, "wake_day": 2000, "wake_minutes": 1}'::jsonb
 where key = 'pro';
update public.plans set limits = limits || '{"wake": null, "wake_day": 3000, "wake_minutes": 1}'::jsonb
 where key = 'max';

alter table public.usage_events drop constraint if exists usage_events_kind_check;
alter table public.usage_events add constraint usage_events_kind_check
    check (kind in ('chat','agent','vision','search','point','talk','dictation','calls','pass','wake'));


-- _heytora_spend: the per-minute brake knows "wake" (speech near the PC can
-- be constant; a check at most every two seconds is plenty).
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

    v_per_min := case p_kind when 'calls' then 120 when 'talk' then 20
                             when 'dictation' then 30 when 'wake' then 30 else 10 end;
    select count(*) into v_recent from public.usage_events e
     where e.user_id = p_uid and e.kind = p_kind and e.created_at > now() - interval '1 minute';
    if v_recent >= v_per_min then
        return jsonb_build_object('ok', false, 'reason', 'rate', 'kind', p_kind, 'plan', v_plan);
    end if;

    v_cap := v_limits -> (p_kind || '_day');
    if jsonb_typeof(v_cap) = 'number' then
        select coalesce(sum(e.quantity), 0)::int into v_today from public.usage_events e
         where e.user_id = p_uid and e.kind = p_kind and e.created_at >= v_day;
        if v_today + p_quantity > (v_cap #>> '{}')::int then
            return jsonb_build_object('ok', false, 'reason', 'day', 'kind', p_kind, 'plan', v_plan,
                                      'limit', (v_cap #>> '{}')::int);
        end if;
    end if;

    select coalesce(sum(e.quantity), 0)::int into v_used from public.usage_events e
     where e.user_id = p_uid and e.kind = p_kind and e.created_at >= v_month;
    if jsonb_typeof(v_limit) = 'number' and v_used + p_quantity > (v_limit #>> '{}')::int then
        return jsonb_build_object('ok', false, 'reason', 'limit', 'kind', p_kind, 'used', v_used,
                                  'limit', (v_limit #>> '{}')::int, 'plan', v_plan);
    end if;

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
    if p_kind not in ('talk', 'dictation', 'agent', 'calls', 'wake') then
        raise exception 'unknown kind: %', p_kind using errcode = '22023';
    end if;
    return public._heytora_spend(v_uid, p_kind, p_quantity, p_meta);
end;
$fn$;


-- The business's report, with the wake word in its own column.
drop function if exists public.heytora_admin_usage(date);
create function public.heytora_admin_usage(p_month date default null)
returns table (
    user_id     uuid,
    email       text,
    plan        text,
    plan_status text,
    talk        int,
    dictation   int,
    agent       int,
    wake        int,
    calls       int,
    cost_usd    numeric,
    last_used   timestamptz
)
language sql
security definer
set search_path = public, pg_temp
as $fn$
    with m as (
        select (date_trunc('month', coalesce(p_month::timestamp, now() at time zone 'UTC'))) at time zone 'UTC' as start
    ), ev as (
        select e.user_id,
               sum(e.quantity) filter (where e.kind = 'talk')::int      as talk,
               sum(e.quantity) filter (where e.kind = 'dictation')::int as dictation,
               sum(e.quantity) filter (where e.kind = 'agent')::int     as agent,
               sum(e.quantity) filter (where e.kind = 'wake')::int      as wake,
               sum(e.quantity) filter (where e.kind = 'calls')::int     as calls,
               max(e.created_at)                                          as last_used
          from public.usage_events e, m
         where e.created_at >= m.start and e.created_at < m.start + interval '1 month'
         group by e.user_id
    ), co as (
        select c.user_id, round(sum(c.cost_micro) / 1000000.0, 4) as cost_usd
          from public.usage_costs c, m
         where c.created_at >= m.start and c.created_at < m.start + interval '1 month'
         group by c.user_id
    )
    select p.id, p.email, p.plan, p.plan_status,
           coalesce(ev.talk, 0), coalesce(ev.dictation, 0), coalesce(ev.agent, 0),
           coalesce(ev.wake, 0), coalesce(ev.calls, 0), coalesce(co.cost_usd, 0), ev.last_used
      from public.profiles p
      left join ev on ev.user_id = p.id
      left join co on co.user_id = p.id
     order by coalesce(co.cost_usd, 0) desc, p.email;
$fn$;

revoke all on function public._heytora_spend(uuid, text, int, jsonb)  from public, anon, authenticated;
revoke all on function public.heytora_consume(text, int, jsonb)       from public, anon;
grant execute on function public.heytora_consume(text, int, jsonb)    to authenticated;
revoke all on function public.heytora_admin_usage(date)               from public, anon, authenticated;
grant execute on function public.heytora_admin_usage(date)            to service_role;

commit;
