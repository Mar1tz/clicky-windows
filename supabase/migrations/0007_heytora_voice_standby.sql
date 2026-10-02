-- =============================================================================
-- HeyTora — the voice waiting for a question is not a question
-- Migration 0007  (needs 0004, 0006)
--
-- Pro and Max keep the voice session open so the first word is heard at
-- once. Its pass had to live in a talk window, so connecting at start-up
-- (and every reconnect) counted a talk message nobody asked: the owner's
-- count rose on launch. Every question is counted when it is heard now; the
-- session waiting for one lives in a STANDBY window instead - kind "voice":
--
--   * only on plans with unlimited talk (Free has no "voice" in its limits,
--     so it is refused there - Free opens the voice on the question anyway);
--   * not a talk message, never on the price list, a hidden daily ceiling
--     and a per-minute brake like any other kind, voice_minutes long;
--   * its passes count against passes_per_talk like a talk window's;
--   * no ticket: a standby window can carry the voice, never an answer
--     from the forwarder - those need a question's talk window.
--
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

update public.plans set limits = limits || '{"voice": null, "voice_day": 300, "voice_minutes": 30}'::jsonb
 where key in ('pro', 'max');

alter table public.usage_events drop constraint if exists usage_events_kind_check;
alter table public.usage_events add constraint usage_events_kind_check
    check (kind in ('chat','agent','vision','search','point','talk','dictation','calls','pass','wake','voice'));


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
    if p_kind not in ('talk', 'agent', 'voice') then
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


create or replace function public.heytora_pass(p_window bigint, p_model text default '')
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid     uuid := auth.uid();
    v_start   timestamptz;
    v_kind    text;
    v_p       jsonb;
    v_minutes int;
    v_cap     int;
    v_n       int;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    perform pg_advisory_xact_lock(hashtext(v_uid::text || ':pass'));

    select e.created_at, e.kind into v_start, v_kind from public.usage_events e
     where e.id = p_window and e.user_id = v_uid and e.kind in ('talk', 'voice');
    if not found then
        return jsonb_build_object('ok', false, 'reason', 'window', 'kind', 'talk');
    end if;
    v_p := public._heytora_plan(v_uid);
    v_minutes := least(greatest(coalesce((v_p -> 'limits' ->> (v_kind || '_minutes'))::int, 30), 1), 120);
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

revoke all on function public.heytora_window(text, boolean, jsonb) from public, anon;
grant execute on function public.heytora_window(text, boolean, jsonb) to authenticated;
revoke all on function public.heytora_pass(bigint, text)           from public, anon;
grant execute on function public.heytora_pass(bigint, text)        to authenticated;

commit;
