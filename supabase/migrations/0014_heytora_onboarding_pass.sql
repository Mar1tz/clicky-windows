-- =============================================================================
-- HeyTora — the first launch's voice is free
-- Migration 0014  (needs 0007, 0011, 0012)       docs/ui/HEYCLICKY_ONBOARDING.md
--
-- The new onboarding talks: Tora hears your name and asks four questions out
-- loud. As talk messages that would use a good part of Free's 50 credits
-- before anyone has asked her anything (the owner, 29 Oct 2026: "onboarding
-- is free"). So its voice lives in an ONBOARDING window instead - a third
-- kind beside "talk" and "voice" (0007):
--
--   * on every plan, never a talk message, never on the price list and never
--     booked against the credits: _heytora_spend books only talk, dictation
--     and wake, and heytora_voice_report takes only talk windows (0011);
--   * onboarding_ever windows per account, EVER (3: a first run and two
--     more - a reinstall, a "Run again"). After that the app asks for a
--     normal talk pass, counted like any other. This, not the app, is what
--     keeps it from being a free voice;
--   * onboarding_minutes long (20), onboarding_passes passes (6: the
--     interview reconnects between steps);
--   * no ticket: like standby, it carries the voice and never an answer from
--     the forwarder.
--
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

update public.plans set limits = limits
    || '{"onboarding": null, "onboarding_ever": 3, "onboarding_minutes": 20, "onboarding_passes": 6}'::jsonb;

alter table public.usage_events drop constraint if exists usage_events_kind_check;
alter table public.usage_events add constraint usage_events_kind_check
    check (kind in ('chat','agent','vision','search','point','talk','dictation','calls','pass','wake','voice',
                    'onboarding'));


-- ── heytora_window: as in 0007, and the onboarding kind ─────────────────────
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
    v_ever    int;
    v_res     jsonb;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    if p_kind not in ('talk', 'agent', 'voice', 'onboarding') then
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

    -- 0014: a free onboarding a few times per account, ever - not per month.
    if p_kind = 'onboarding' then
        select count(*) into v_ever from public.usage_events e
         where e.user_id = v_uid and e.kind = 'onboarding';
        if v_ever >= coalesce((v_p -> 'limits' ->> 'onboarding_ever')::int, 0) then
            return jsonb_build_object('ok', false, 'reason', 'onboarding_used', 'kind', p_kind,
                                      'plan', v_p ->> 'plan');
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


-- ── heytora_pass: as in 0011, onboarding windows too, with their own cap ────
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
     where e.id = p_window and e.user_id = v_uid and e.kind in ('talk', 'voice', 'onboarding');
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

    v_cap := coalesce((v_p -> 'limits' ->> (v_kind || '_passes'))::int,
                      (v_p -> 'limits' ->> 'passes_per_talk')::int, 3);
    select count(*) into v_n from public.usage_events e
     where e.user_id = v_uid and e.kind = 'pass' and e.meta ->> 'window' = p_window::text;
    if v_n >= v_cap then
        return jsonb_build_object('ok', false, 'reason', 'passes', 'kind', 'talk', 'plan', v_p ->> 'plan');
    end if;

    insert into public.usage_events (user_id, kind, quantity, meta)
    values (v_uid, 'pass', 1, jsonb_build_object('window', p_window, 'model', left(coalesce(p_model, ''), 80)));
    if v_kind = 'talk' then
        perform public._heytora_book(v_uid, 'talk', p_window);       -- 0011: once per question
    end if;
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
