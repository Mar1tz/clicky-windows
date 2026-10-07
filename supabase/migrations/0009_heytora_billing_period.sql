-- =============================================================================
-- HeyTora — a paid plan's allowance runs with its billing period
-- Migration 0009  (needs 0008)
--
-- Until now every allowance reset on the 1st of the month, while a paid plan
-- renews on the day it was bought: "renews on 6 Nov" beside "starts again on
-- 1 Nov", and someone who paid on the 25th had six days of their month. Now
-- a paid plan that is active counts from the start of its current billing
-- period (Paddle's current_billing_period.starts_at, stored by the webhook)
-- and starts again when it renews. Free - and a paid plan that has lapsed -
-- keeps the calendar month.
--
-- _heytora_period(user) is the one place that decides; the limits
-- (_heytora_spend) and what the app shows (get_usage_summary) both ask it.
-- A stored period that has already ended (a missed webhook) is not trusted:
-- the calendar month is used until the next event sets it right.
--
-- Usage events are not touched - only where counting starts.
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

alter table public.profiles add column if not exists plan_period_start timestamptz;

-- Subscriptions from before this migration: their period's start, from the
-- last event applied to them (kept whole in billing_events, 0008).
update public.profiles p
   set plan_period_start = (
       select (e.payload #>> '{data,current_billing_period,starts_at}')::timestamptz
         from public.billing_events e
        where e.user_id = p.id and e.applied
          and e.payload #>> '{data,current_billing_period,starts_at}' is not null
        order by e.occurred_at desc
        limit 1)
 where p.paddle_subscription_id is not null and p.plan_period_start is null;


-- ── Whose month is it ─────────────────────────────────────────────────────────
create or replace function public._heytora_period(p_uid uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_month timestamptz := (date_trunc('month', now() at time zone 'UTC')) at time zone 'UTC';
    v_prof  public.profiles%rowtype;
    v_end   timestamptz;
begin
    select * into v_prof from public.profiles where id = p_uid;
    if found and v_prof.plan <> 'free' and v_prof.plan_status in ('active', 'trialing')
       and v_prof.plan_period_start is not null and v_prof.plan_period_start <= now() then
        v_end := coalesce(v_prof.plan_renews_at, v_prof.plan_cancel_at,
                          v_prof.plan_period_start + interval '1 month');
        if v_end > now() then
            return jsonb_build_object('start', v_prof.plan_period_start, 'end', v_end, 'billing', true);
        end if;
    end if;
    return jsonb_build_object('start', v_month, 'end', v_month + interval '1 month', 'billing', false);
end;
$fn$;


-- ── The limits: _heytora_spend as in 0006, counting from the period ─────────
create or replace function public._heytora_spend(
    p_uid uuid, p_kind text, p_quantity int, p_meta jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_month   timestamptz := (public._heytora_period(p_uid) ->> 'start')::timestamptz;
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


-- ── The webhook also stores the period's start ───────────────────────────────
drop function if exists public.heytora_billing_event(text, text, timestamptz, uuid, text, text, text, text, timestamptz, timestamptz, jsonb);
create or replace function public.heytora_billing_event(
    p_event_id     text,
    p_type         text,
    p_occurred_at  timestamptz,
    p_user         uuid,
    p_customer     text,
    p_subscription text,
    p_plan         text,
    p_status       text,
    p_renews_at    timestamptz,
    p_cancel_at    timestamptz,
    p_payload      jsonb,
    p_period_start timestamptz default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid  uuid := p_user;
    v_last timestamptz;
begin
    insert into public.billing_events (id, type, occurred_at, payload)
    values (p_event_id, p_type, p_occurred_at, coalesce(p_payload, '{}'::jsonb))
    on conflict (id) do nothing;
    if not found then
        return jsonb_build_object('applied', false, 'note', 'already seen');
    end if;

    if v_uid is null and p_customer is not null then
        select id into v_uid from public.profiles where paddle_customer_id = p_customer limit 1;
    end if;
    if v_uid is null or not exists (select 1 from public.profiles where id = v_uid) then
        update public.billing_events set note = 'no such user' where id = p_event_id;
        return jsonb_build_object('applied', false, 'note', 'no such user');
    end if;

    select billing_event_at into v_last from public.profiles where id = v_uid;
    if v_last is not null and p_occurred_at < v_last then
        update public.billing_events set user_id = v_uid, note = 'older than the last applied'
         where id = p_event_id;
        return jsonb_build_object('applied', false, 'note', 'older than the last applied');
    end if;
    if p_plan is not null and not exists (select 1 from public.plans where key = p_plan) then
        update public.billing_events set user_id = v_uid, note = 'unknown plan ' || p_plan
         where id = p_event_id;
        return jsonb_build_object('applied', false, 'note', 'unknown plan');
    end if;
    if p_status not in ('active', 'trialing', 'past_due', 'canceled') then
        raise exception 'unknown plan status %', p_status;
    end if;

    update public.profiles set
        plan                   = coalesce(p_plan, plan),
        plan_status            = p_status,
        plan_renews_at         = p_renews_at,
        plan_cancel_at         = p_cancel_at,
        plan_period_start      = p_period_start,
        paddle_customer_id     = coalesce(p_customer, paddle_customer_id),
        paddle_subscription_id = coalesce(p_subscription, paddle_subscription_id),
        billing_event_at       = p_occurred_at,
        updated_at             = now()
     where id = v_uid;
    update public.billing_events set user_id = v_uid, applied = true where id = p_event_id;
    return jsonb_build_object('applied', true, 'user_id', v_uid);
end;
$fn$;


-- ── What the app shows: get_usage_summary as in 0008, over the period ───────
create or replace function public.get_usage_summary()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid     uuid := auth.uid();
    v_period  jsonb := public._heytora_period(v_uid);
    v_month   timestamptz := (v_period ->> 'start')::timestamptz;
    v_day     timestamptz := (date_trunc('day',   now() at time zone 'UTC')) at time zone 'UTC';
    v_prof    public.profiles%rowtype;
    v_p       jsonb;
    v_all     jsonb;
    v_shown   text[] := array['talk', 'dictation', 'agent'];
    v_limits  jsonb := '{}'::jsonb;
    v_month_n jsonb;
    v_today_n jsonb;
    v_blocked jsonb := '{}'::jsonb;
    v_spent   bigint;
    v_budget  numeric;
    v_fair    numeric;
    v_used    int;
    v_cap     jsonb;
    k         text;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    v_p := public._heytora_plan(v_uid);
    v_all := v_p -> 'limits';
    select * into v_prof from public.profiles where id = v_uid;

    select coalesce(jsonb_object_agg(s.kind, s.total), '{}'::jsonb) into v_month_n
      from (select e.kind, sum(e.quantity)::int as total from public.usage_events e
             where e.user_id = v_uid and e.created_at >= v_month and e.kind = any(v_shown)
             group by e.kind) s;
    select coalesce(jsonb_object_agg(s.kind, s.total), '{}'::jsonb) into v_today_n
      from (select e.kind, sum(e.quantity)::int as total from public.usage_events e
             where e.user_id = v_uid and e.created_at >= v_day and e.kind = any(v_shown)
             group by e.kind) s;

    foreach k in array v_shown loop
        if v_all ? k then
            v_limits := v_limits || jsonb_build_object(k, v_all -> k);
            -- The month's allowance, then today's fair use: what would refuse it now.
            v_used := coalesce((v_month_n ->> k)::int, 0);
            if jsonb_typeof(v_all -> k) = 'number' and v_used >= (v_all ->> k)::int then
                v_blocked := v_blocked || jsonb_build_object(k, 'limit');
            else
                v_cap := v_all -> (k || '_day');
                if jsonb_typeof(v_cap) = 'number'
                   and coalesce((v_today_n ->> k)::int, 0) >= (v_cap #>> '{}')::int then
                    v_blocked := v_blocked || jsonb_build_object(k, 'day');
                end if;
            end if;
        end if;
    end loop;

    if jsonb_typeof(v_all -> 'budget_usd') = 'number' then
        v_budget := (v_all ->> 'budget_usd')::numeric * 1000000;
        select coalesce(sum(c.cost_micro), 0) into v_spent from public.usage_costs c
         where c.user_id = v_uid and c.created_at >= v_month;
        v_fair := case when v_budget > 0 then least(round(v_spent / v_budget, 3), 1) else 1 end;
        if v_spent >= v_budget then
            v_blocked := v_blocked || jsonb_build_object('all', 'budget');
        end if;
    end if;

    return jsonb_build_object(
        'user_id',        v_uid,
        'email',          v_prof.email,
        'display_name',   v_prof.display_name,
        'plan',           v_p ->> 'plan',            -- what applies now (lapsed = free)
        'plan_name',      coalesce((select pl.name from public.plans pl where pl.key = v_p ->> 'plan'),
                                   initcap(v_p ->> 'plan')),
        'plan_chosen',    v_prof.plan,
        'plan_status',    v_prof.plan_status,
        'plan_renews_at', v_prof.plan_renews_at,
        'plan_cancel_at', v_prof.plan_cancel_at,   -- a paid plan ending then (0008)
        'billing',        v_prof.paddle_customer_id is not null,  -- there is billing to manage
        'period_start',   v_month,
        'period_end',     (v_period ->> 'end')::timestamptz,
        'day_resets_at',  extract(epoch from v_day + interval '1 day'),
        'now',            extract(epoch from now()),
        'limits',         v_limits,
        'usage',          v_month_n,
        'today',          v_today_n,
        'blocked',        v_blocked,
        'fair_use',       v_fair
    );
end;
$fn$;


revoke all on function public._heytora_period(uuid) from public, anon, authenticated;
revoke all on function public._heytora_spend(uuid, text, int, jsonb) from public, anon, authenticated;
revoke all on function public.heytora_billing_event(text, text, timestamptz, uuid, text, text, text, text, timestamptz, timestamptz, jsonb, timestamptz) from public, anon, authenticated;
grant execute on function public.heytora_billing_event(text, text, timestamptz, uuid, text, text, text, text, timestamptz, timestamptz, jsonb, timestamptz) to service_role;
revoke all on function public.get_usage_summary()    from public, anon;
grant execute on function public.get_usage_summary() to authenticated;

commit;
