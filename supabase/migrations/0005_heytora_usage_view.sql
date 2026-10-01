-- =============================================================================
-- HeyTora — what a user (and the business) can see of the plan
-- Migration 0005  (needs 0004)
--
-- get_usage_summary() now says everything the app and the website need to
-- show the plan honestly, without the server's own numbers:
--   usage / limits   this month, for talk, dictation and agent
--   today            today's use (UTC) of the same three
--   blocked          {kind: "limit" | "day"} for anything refused right now,
--                    and "all": "budget" when the month's fair use is spent
--   fair_use         0..1, how much of the month's overall allowance is used
--                    (the cost ceiling, as a share - never in dollars)
--   period_end, day_resets_at, now   when things reset, and the server's
--                    clock (epoch seconds) so the app is not misled by its own
--
-- heytora_admin_usage(month) is the business's view: every user, their plan,
-- what they used and what it cost. Service role only (the dashboard's SQL
-- editor, or the website's server) - never a signed-in user.
--
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

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


-- ── The business's view ───────────────────────────────────────────────────────
create or replace function public.heytora_admin_usage(p_month date default null)
returns table (
    user_id    uuid,
    email      text,
    plan       text,
    plan_status text,
    talk       int,
    dictation  int,
    agent      int,
    calls      int,
    cost_usd   numeric,
    last_used  timestamptz
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
           coalesce(ev.calls, 0), coalesce(co.cost_usd, 0), ev.last_used
      from public.profiles p
      left join ev on ev.user_id = p.id
      left join co on co.user_id = p.id
     order by coalesce(co.cost_usd, 0) desc, p.email;
$fn$;

revoke all on function public.get_usage_summary()              from public, anon;
grant execute on function public.get_usage_summary()           to authenticated;
revoke all on function public.heytora_admin_usage(date)        from public, anon, authenticated;
grant execute on function public.heytora_admin_usage(date)     to service_role;

commit;
