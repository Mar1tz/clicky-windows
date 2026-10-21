-- =============================================================================
-- HeyTora — credits: the month's pot, shown, and credits that can be bought
-- Migration 0012  (needs 0009, 0010, 0011)
--
-- The pot (budget_usd, 0011) becomes something the user sees and can add to
-- (the owners, 21 Oct 2026):
--
--   * 1 credit = 1 US cent of what Tora costs us. A plan's month is its
--     budget_usd in credits: Free 50, Pro trial 600, Pro 900, Max 4,700.
--     Credits are THE limit users see; the counted allowances stay only as
--     hidden brakes, set above what the credits buy (Free: 100 questions
--     against ~25 that 50 credits buy).
--   * Bought credits: packs, each a one-off Paddle price of the "HeyTora
--     credits" product with custom data {"credits": n} - 675 for $10, 1,750
--     for $25, 3,500 for $50, about 25% margin after Paddle's fee. Paid
--     plans only (the server checks). They never expire.
--   * The month's credits are spent first, then bought ones. Every cost row
--     says how much of it came from bought credits (topup_micro, set by a
--     trigger as the row is written), so the balance is a sum, never a
--     counter that can drift.
--   * Nothing new opens once the month's credits AND the bought ones are
--     used up (_heytora_spend); get_usage_summary shows both.
--
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

-- ── Free: 50 credits a month (the owners, 21 Oct) ────────────────────────────
-- About the 25 questions Free had; the count goes above it so the credits
-- are what runs out first.
update public.plans set limits = limits || '{"budget_usd": 0.5, "talk": 100}'::jsonb where key = 'free';


-- ── Bought credits ───────────────────────────────────────────────────────────
-- One row per paid Paddle transaction: a webhook that comes twice adds once.
create table if not exists public.credit_topups (
    id          bigint generated always as identity primary key,
    user_id     uuid        not null references auth.users(id) on delete cascade,
    credits     int         not null check (credits > 0),
    paddle_txn  text        not null unique,
    created_at  timestamptz not null default now()
);
create index if not exists credit_topups_user_idx on public.credit_topups (user_id);
alter table public.credit_topups enable row level security;   -- no policies: the server only

-- How much of each cost came out of bought credits.
alter table public.usage_costs add column if not exists topup_micro bigint not null default 0
    check (topup_micro >= 0);


-- ── Where an account stands: the month's credits and the bought ones ─────────
-- In millionths of a dollar (1 credit = 10,000). plan_micro is null on a
-- plan without a pot.
create or replace function public._heytora_credits(p_uid uuid)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_month  timestamptz := (public._heytora_period(p_uid) ->> 'start')::timestamptz;
    v_limits jsonb := public._heytora_plan(p_uid) -> 'limits';
    v_plan   bigint;
    v_used   bigint;
    v_bought bigint;
    v_spent  bigint;
begin
    if jsonb_typeof(v_limits -> 'budget_usd') = 'number' then
        v_plan := ((v_limits ->> 'budget_usd')::numeric * 1000000)::bigint;
    end if;
    select coalesce(sum(c.cost_micro - c.topup_micro), 0) into v_used from public.usage_costs c
     where c.user_id = p_uid and c.created_at >= v_month;
    select coalesce(sum(t.credits), 0)::bigint * 10000 into v_bought from public.credit_topups t
     where t.user_id = p_uid;
    select coalesce(sum(c.topup_micro), 0) into v_spent from public.usage_costs c
     where c.user_id = p_uid;
    return jsonb_build_object(
        'plan_micro',  v_plan,
        'used_micro',  v_used,
        'plan_left',   case when v_plan is null then null else greatest(v_plan - v_used, 0) end,
        'topup_left',  greatest(v_bought - v_spent, 0));
end;
$fn$;


-- ── Each cost is paid from the month's credits, then from bought ones ───────
create or replace function public._heytora_cost_split()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_c    jsonb;
    v_rest bigint;
begin
    if coalesce(new.cost_micro, 0) <= 0 then
        new.topup_micro := 0;
        return new;
    end if;
    -- One cost at a time per account, so two can't spend the same credit.
    perform pg_advisory_xact_lock(hashtext(new.user_id::text || ':credits'));
    v_c := public._heytora_credits(new.user_id);
    if v_c -> 'plan_micro' = 'null'::jsonb then
        new.topup_micro := 0;                                   -- no pot: nothing to split
        return new;
    end if;
    v_rest := greatest(new.cost_micro - (v_c ->> 'plan_left')::bigint, 0);
    new.topup_micro := least(v_rest, (v_c ->> 'topup_left')::bigint);
    return new;
end;
$fn$;

drop trigger if exists usage_costs_split on public.usage_costs;
create trigger usage_costs_split before insert on public.usage_costs
    for each row execute function public._heytora_cost_split();


-- ── _heytora_spend: as in 0011, refused only when both are used up ──────────
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
    v_c       jsonb;
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

    -- 0012: the month's credits, then bought ones; refused when both are gone.
    v_c := public._heytora_credits(p_uid);
    if v_c -> 'plan_left' <> 'null'::jsonb
       and (v_c ->> 'plan_left')::bigint <= 0 and (v_c ->> 'topup_left')::bigint <= 0 then
        return jsonb_build_object('ok', false, 'reason', 'budget', 'kind', p_kind, 'plan', v_plan);
    end if;

    insert into public.usage_events (user_id, kind, quantity, meta)
    values (p_uid, p_kind, p_quantity, coalesce(p_meta, '{}'::jsonb))
    returning id into v_id;

    -- 0011: a use that goes straight to Google is booked as it opens.
    if p_kind in ('dictation', 'wake') or (p_kind = 'talk' and public._heytora_live_open(p_uid)) then
        perform public._heytora_book(p_uid, p_kind, v_id, p_quantity);
    end if;

    return jsonb_build_object(
        'ok', true, 'id', v_id, 'kind', p_kind, 'used', v_used + p_quantity, 'plan', v_plan,
        'limit', case when jsonb_typeof(v_limit) = 'number' then (v_limit #>> '{}')::int end,
        'limits', v_limits);
end;
$fn$;


-- ── heytora_credit_grant: a paid top-up, from the webhook ────────────────────
create or replace function public.heytora_credit_grant(p_user uuid, p_credits int, p_txn text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_id bigint;
begin
    if p_user is null or coalesce(p_credits, 0) <= 0 or coalesce(p_txn, '') = '' then
        return jsonb_build_object('ok', false, 'reason', 'bad');
    end if;
    if not exists (select 1 from auth.users u where u.id = p_user) then
        return jsonb_build_object('ok', false, 'reason', 'no_user');
    end if;
    insert into public.credit_topups (user_id, credits, paddle_txn)
    values (p_user, least(p_credits, 100000), p_txn)
    on conflict (paddle_txn) do nothing
    returning id into v_id;
    return jsonb_build_object('ok', true, 'granted', v_id is not null,
                              'topup_left', (public._heytora_credits(p_user) ->> 'topup_left')::bigint / 10000);
end;
$fn$;


-- ── get_usage_summary: as in 0010, with the credits ──────────────────────────
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
    v_c       jsonb;
    v_credits jsonb;
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

    -- 0012: credits. 1 credit = 1 cent; the month's first, then bought ones.
    v_c := public._heytora_credits(v_uid);
    if v_c -> 'plan_micro' <> 'null'::jsonb then
        v_fair := case when (v_c ->> 'plan_micro')::bigint > 0
                       then least(round((v_c ->> 'used_micro')::numeric / (v_c ->> 'plan_micro')::bigint, 3), 1)
                       else 1 end;
        if (v_c ->> 'plan_left')::bigint <= 0 and (v_c ->> 'topup_left')::bigint <= 0 then
            v_blocked := v_blocked || jsonb_build_object('all', 'budget');
        end if;
        v_credits := jsonb_build_object(
            'month',      (v_c ->> 'plan_micro')::bigint / 10000,
            'month_used', least(ceil((v_c ->> 'used_micro')::numeric / 10000), (v_c ->> 'plan_micro')::bigint / 10000)::int,
            'month_left', floor((v_c ->> 'plan_left')::numeric / 10000)::int,
            'bought_left', floor((v_c ->> 'topup_left')::numeric / 10000)::int,
            'can_buy',    (v_p ->> 'plan') in ('pro', 'max'));
    end if;

    return jsonb_build_object(
        'user_id',        v_uid,
        'email',          v_prof.email,
        'display_name',   v_prof.display_name,
        'plan',           v_p ->> 'plan',
        'plan_name',      coalesce((select pl.name from public.plans pl where pl.key = v_p ->> 'plan'),
                                   initcap(v_p ->> 'plan')),
        'plan_chosen',    v_prof.plan,
        'plan_status',    v_prof.plan_status,
        'plan_renews_at', v_prof.plan_renews_at,
        'plan_cancel_at', v_prof.plan_cancel_at,
        'trial_until',    case when v_p ->> 'plan' = 'pro_trial' then v_prof.trial_until end,
        'billing',        v_prof.paddle_customer_id is not null,
        'period_start',   v_month,
        'period_end',     (v_period ->> 'end')::timestamptz,
        'day_resets_at',  extract(epoch from v_day + interval '1 day'),
        'now',            extract(epoch from now()),
        'limits',         v_limits,
        'usage',          v_month_n,
        'today',          v_today_n,
        'blocked',        v_blocked,
        'fair_use',       v_fair,
        'credits',        v_credits                 -- 0012; null on a plan without a pot
    );
end;
$fn$;

revoke all on function public._heytora_credits(uuid)                 from public, anon, authenticated;
revoke all on function public._heytora_cost_split()                  from public, anon, authenticated;
revoke all on function public._heytora_spend(uuid, text, int, jsonb) from public, anon, authenticated;
revoke all on function public.heytora_credit_grant(uuid, int, text)  from public, anon, authenticated;
grant execute on function public.heytora_credit_grant(uuid, int, text) to service_role;

commit;
