-- =============================================================================
-- HeyTora — the three plans the website sells, and what the app may use on them
-- Migration 0003  (needs 0001)
--
-- The website creates the account and sets profiles.plan ('free' | 'pro' |
-- 'max') and profiles.plan_status when someone picks or pays for a plan -
-- with the service_role key, on the server, never from the app. The app signs
-- in to that same account; the `heytora` server function reads the plan from
-- here and enforces it.
--
-- What is counted (per calendar month, UTC):
--   talk       a live conversation the app opens - one voice pass. On Free a
--              question opens one (a quick follow-up inside 20 s rides on it).
--   dictation  a dictation (speech to text) session.
--   agent      an agent ticket: up to 30 minutes of agent work.
--   calls      every other request through the server - seeing the screen,
--              planning a walkthrough, the step voice, the agents' thinking.
--              Not on the price list: a backstop, so no plan can be used to
--              drain the business key.
-- A JSON null limit is unlimited. A kind missing from a plan is NOT allowed.
--
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

-- ── The plans (prices live on the website; these are the allowances) ─────────
insert into public.plans (key, name, limits, sort_order) values
    ('free', 'Free', '{"talk": 25,   "dictation": 300,  "agent": 25,   "calls": 1500}'::jsonb, 0),
    ('pro',  'Pro',  '{"talk": null, "dictation": null, "agent": 150,  "calls": 30000}'::jsonb, 1),
    ('max',  'Max',  '{"talk": null, "dictation": null, "agent": 1000, "calls": 150000}'::jsonb, 2)
on conflict (key) do update
    set name = excluded.name, limits = excluded.limits, sort_order = excluded.sort_order;


-- ── The new kinds of usage ────────────────────────────────────────────────────
alter table public.usage_events drop constraint if exists usage_events_kind_check;
alter table public.usage_events add constraint usage_events_kind_check
    check (kind in ('chat','agent','vision','search','point','talk','dictation','calls'));


-- ── heytora_consume() ─────────────────────────────────────────────────────────
-- Check the allowance and count one use, atomically - the ONLY way the server
-- function spends a user's plan. Called with the user's own JWT, so auth.uid()
-- is them; a user can only ever spend their own allowance.
--
-- Returns {ok, kind, used, limit, plan} or {ok:false, reason:'limit'|'rate'|...}.
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
    v_uid     uuid := auth.uid();
    v_start   timestamptz := (date_trunc('month', now() at time zone 'UTC')) at time zone 'UTC';
    v_plan    text;
    v_status  text;
    v_limits  jsonb;
    v_limit   jsonb;
    v_used    int;
    v_recent  int;
    v_per_min int;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    if p_kind not in ('talk','dictation','agent','calls') then
        raise exception 'unknown kind: %', p_kind using errcode = '22023';
    end if;
    p_quantity := least(greatest(coalesce(p_quantity, 1), 1), 100);

    -- One at a time per user and kind, so two requests cannot both see the
    -- last unit of an allowance as free.
    perform pg_advisory_xact_lock(hashtext(v_uid::text || ':' || p_kind));

    select p.plan, p.plan_status into v_plan, v_status from public.profiles p where p.id = v_uid;
    if not found then
        insert into public.profiles (id, email)
        values (v_uid, (select email from auth.users where id = v_uid))
        on conflict (id) do nothing;
        v_plan := 'free';
        v_status := 'active';
    end if;
    -- A lapsed or cancelled subscription is the Free plan until it is fixed.
    if v_status not in ('active', 'trialing') then
        v_plan := 'free';
    end if;

    select coalesce(limits, '{}'::jsonb) into v_limits from public.plans where key = v_plan;
    if v_limits is null or not (v_limits ? p_kind) then
        return jsonb_build_object('ok', false, 'reason', 'not_in_plan', 'kind', p_kind, 'plan', v_plan);
    end if;
    v_limit := v_limits -> p_kind;

    -- A brake on bursts, on every plan: a stuck or patched app cannot burn a
    -- month in a minute.
    v_per_min := case p_kind when 'calls' then 120 when 'talk' then 20
                             when 'dictation' then 30 else 10 end;
    select count(*) into v_recent from public.usage_events
     where user_id = v_uid and kind = p_kind and created_at > now() - interval '1 minute';
    if v_recent >= v_per_min then
        return jsonb_build_object('ok', false, 'reason', 'rate', 'kind', p_kind, 'plan', v_plan);
    end if;

    select coalesce(sum(quantity), 0)::int into v_used from public.usage_events
     where user_id = v_uid and kind = p_kind and created_at >= v_start;

    if jsonb_typeof(v_limit) = 'number' and v_used + p_quantity > (v_limit #>> '{}')::int then
        return jsonb_build_object('ok', false, 'reason', 'limit', 'kind', p_kind, 'used', v_used,
                                  'limit', (v_limit #>> '{}')::int, 'plan', v_plan);
    end if;

    insert into public.usage_events (user_id, kind, quantity, meta)
    values (v_uid, p_kind, p_quantity, coalesce(p_meta, '{}'::jsonb));

    return jsonb_build_object(
        'ok', true, 'kind', p_kind, 'used', v_used + p_quantity, 'plan', v_plan,
        'limit', case when jsonb_typeof(v_limit) = 'number' then (v_limit #>> '{}')::int end);
end;
$fn$;

revoke all on function public.heytora_consume(text, int, jsonb) from public, anon;
grant execute on function public.heytora_consume(text, int, jsonb) to authenticated;

commit;
