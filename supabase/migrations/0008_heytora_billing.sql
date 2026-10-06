-- =============================================================================
-- HeyTora — paid plans through Paddle
-- Migration 0008  (needs 0005)
--
-- Paddle is the merchant of record: it takes the payment, the tax and the
-- receipts. All HeyTora keeps is what decides the plan - who the Paddle
-- customer and subscription are, the plan's status, when it renews or ends.
--
-- Plan changes still come from the server only (0001's rule): the paddle
-- function verifies Paddle's signature on each webhook and calls
-- heytora_billing_event() with the service role. A signed-in user can read
-- their own profile, never write it.
--
-- Every event is kept once (billing_events, by Paddle's event id): Paddle
-- retries, and may send events out of order, so a repeat is ignored and an
-- event older than the last one applied changes nothing.
--
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

alter table public.profiles add column if not exists paddle_customer_id     text;
alter table public.profiles add column if not exists paddle_subscription_id text;
alter table public.profiles add column if not exists plan_cancel_at         timestamptz;
alter table public.profiles add column if not exists billing_event_at       timestamptz;
create index if not exists profiles_paddle_customer on public.profiles (paddle_customer_id);


-- ── What Paddle told us ──────────────────────────────────────────────────────
create table if not exists public.billing_events (
    id           text primary key,             -- Paddle's event_id
    type         text        not null,
    occurred_at  timestamptz not null,
    user_id      uuid        references auth.users(id) on delete set null,
    applied      boolean     not null default false,
    note         text,
    payload      jsonb       not null,
    received_at  timestamptz not null default now()
);
alter table public.billing_events enable row level security;   -- no policy: service role only
revoke all on public.billing_events from anon, authenticated;


-- ── Applying one ─────────────────────────────────────────────────────────────
-- p_user is our user id (the checkout's custom_data); when it is missing
-- (an event Paddle started, like a renewal) the Paddle customer finds them.
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
    p_payload      jsonb
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
        paddle_customer_id     = coalesce(p_customer, paddle_customer_id),
        paddle_subscription_id = coalesce(p_subscription, paddle_subscription_id),
        billing_event_at       = p_occurred_at,
        updated_at             = now()
     where id = v_uid;
    update public.billing_events set user_id = v_uid, applied = true where id = p_event_id;
    return jsonb_build_object('applied', true, 'user_id', v_uid);
end;
$fn$;

revoke all on function public.heytora_billing_event(text, text, timestamptz, uuid, text, text, text,
                                                    text, timestamptz, timestamptz, jsonb)
    from public, anon, authenticated;
grant execute on function public.heytora_billing_event(text, text, timestamptz, uuid, text, text, text,
                                                       text, timestamptz, timestamptz, jsonb)
    to service_role;


-- ── The app's view: get_usage_summary() as in 0005, plus when a cancelled
--    plan ends and whether there is billing to manage ─────────────────────────
create or replace function public.get_usage_summary()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid     uuid := auth.uid();
    v_month   timestamptz := (date_trunc('month', now() at time zone 'UTC')) at time zone 'UTC';
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
        'period_end',     v_month + interval '1 month',
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

revoke all on function public.get_usage_summary()    from public, anon;
grant execute on function public.get_usage_summary() to authenticated;

commit;
