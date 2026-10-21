-- =============================================================================
-- HeyTora — the voice inside the cost ceiling
-- Migration 0011  (needs 0004, 0006, 0007, 0009, 0010)
--
-- The live voice, dictation and the wake word go straight from the app to
-- Google, so the server never saw what they cost: budget_usd held only the
-- forwarded calls (looks, agents), and the voice was limited by counts alone
-- - a Pro user on the daily limit could cost ~$90 a month on $20.
--
-- Google bills the voice per ANSWER, not per minute: a session left open
-- with nobody talking costs nothing (Pro keeps one open all day). And every
-- question already opens a talk window on the server, on every plan and in
-- every app version. So the question is the unit:
--
--   * Each use is BOOKED as it opens, at direct_cost_estimates' price - a
--     question (talk window) at the long-session average, a dictation, a
--     wake-word check. The server does it; no app can skip it.
--   * A question is booked only when it is answered by the LIVE voice: a
--     live pass of the user's is still running, or one opens in its window.
--     The other route (the cascade: transcribe, then Gemini through this
--     server, then its TTS through this server) is already priced call by
--     call from Google's own counts; booking it as well would charge it
--     twice. Its transcription is a dictation pass, booked as one.
--   * After each answer the app sends Google's own usageMetadata; the server
--     prices it (rules.ts liveCostMicro) and heytora_voice_report adds what
--     the question's answers come to ABOVE its booking - never less.
--   * The ceiling is checked before ANY use opens, not only forwarded calls.
--   * budget_usd at a 50% margin (the owners, 21 Oct 2026): Pro $9 and Max
--     $47 of the $18.50 / $94.50 left after Paddle's fee.
--
-- Measured 21 Oct with tools/voice_eval.py: $0.0099 a fresh answer, $0.0147
-- an answer deep in a long session (--conversation; $0.0084 - $0.0252).
--
-- Not covered: an app changed to talk through the standby session without
-- opening questions. Only routing the voice through the server would stop
-- that; the daily check of the roadmap watches for its trace instead (many
-- standby windows, few questions).
--
-- Idempotent: safe to run more than once. Edited estimates are kept.
-- =============================================================================

begin;

-- ── What a use that goes straight to Google is booked at ─────────────────────
-- Not per plan: what Google charges. Edit a row to change a booking - no
-- release, no deploy.
create table if not exists public.direct_cost_estimates (
    kind text    primary key,
    usd  numeric not null check (usd >= 0),
    note text    not null default ''
);
alter table public.direct_cost_estimates enable row level security;   -- no policies: the server only

insert into public.direct_cost_estimates (kind, usd, note) values
    ('talk',      0.015,  'one question: $0.0147 an answer deep in a long session (voice_eval, 21 Oct); dearer answers are reported'),
    ('dictation', 0.005,  'about 30 s of speech to text; not reported by the app yet'),
    ('wake',      0.0005, 'one wake-word check; not reported by the app yet')
on conflict (kind) do nothing;


-- ── One ledger: which question a cost belongs to, and what the app reported ──
-- A report's row carries the answer's full price (reported_micro) and adds
-- only what takes the question past its booking (cost_micro), so
-- sum(cost_micro) stays what the month cost.
alter table public.usage_costs add column if not exists window_id bigint;
alter table public.usage_costs add column if not exists reported_micro bigint not null default 0
    check (reported_micro >= 0);
create index if not exists usage_costs_window_idx on public.usage_costs (window_id) where window_id is not null;


-- ── The ceilings: 50% of what each plan leaves after Paddle ──────────────────
update public.plans set limits = limits || '{"budget_usd": 9}'::jsonb  where key = 'pro';
update public.plans set limits = limits || '{"budget_usd": 47}'::jsonb where key = 'max';


-- ── Is the live voice open? A live pass whose window hasn't ended ────────────
-- Talk and standby passes are the live voice (heytora_pass); the cascade's
-- transcriber is a dictation pass and never one of these.
create or replace function public._heytora_live_open(p_uid uuid)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_limits jsonb := public._heytora_plan(p_uid) -> 'limits';
begin
    return exists (
        select 1
          from public.usage_events p
          join public.usage_events w on w.id = (p.meta ->> 'window')::bigint and w.user_id = p_uid
         where p.user_id = p_uid and p.kind = 'pass'
           and p.created_at > now() - interval '2 hours'           -- no window is longer
           and w.kind in ('talk', 'voice')
           and w.created_at + make_interval(mins => least(greatest(
                   coalesce((v_limits ->> (w.kind || '_minutes'))::int, 30), 1), 120)) > now());
end;
$fn$;


-- ── Book a use once, against what it opened ──────────────────────────────────
create or replace function public._heytora_book(p_uid uuid, p_kind text, p_window bigint, p_quantity int default 1)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_est numeric;
begin
    if exists (select 1 from public.usage_costs c
                where c.window_id = p_window and c.user_id = p_uid and c.model = 'estimate') then
        return;                                                    -- booked already
    end if;
    select d.usd into v_est from public.direct_cost_estimates d where d.kind = p_kind;
    if coalesce(v_est, 0) > 0 then
        insert into public.usage_costs (user_id, kind, model, cost_micro, window_id)
        values (p_uid, p_kind, 'estimate',
                ceil(v_est * greatest(coalesce(p_quantity, 1), 1) * 1000000)::bigint, p_window);
    end if;
end;
$fn$;


-- ── _heytora_spend: as in 0009, the ceiling on every kind, and the booking ───
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

    -- 0011: every kind, not only forwarded calls - the voice is in the total now.
    if jsonb_typeof(v_limits -> 'budget_usd') = 'number' then
        select coalesce(sum(c.cost_micro), 0) into v_spent from public.usage_costs c
         where c.user_id = p_uid and c.created_at >= v_month;
        if v_spent >= ((v_limits ->> 'budget_usd')::numeric * 1000000)::bigint then
            return jsonb_build_object('ok', false, 'reason', 'budget', 'kind', p_kind, 'plan', v_plan);
        end if;
    end if;

    insert into public.usage_events (user_id, kind, quantity, meta)
    values (p_uid, p_kind, p_quantity, coalesce(p_meta, '{}'::jsonb))
    returning id into v_id;

    -- 0011: a use that goes straight to Google is booked as it opens - a
    -- question only when the live voice answers it (else heytora_pass books
    -- it if a live pass opens in its window).
    if p_kind in ('dictation', 'wake') or (p_kind = 'talk' and public._heytora_live_open(p_uid)) then
        perform public._heytora_book(p_uid, p_kind, v_id, p_quantity);
    end if;

    return jsonb_build_object(
        'ok', true, 'id', v_id, 'kind', p_kind, 'used', v_used + p_quantity, 'plan', v_plan,
        'limit', case when jsonb_typeof(v_limit) = 'number' then (v_limit #>> '{}')::int end,
        'limits', v_limits);
end;
$fn$;


-- ── heytora_pass: as in 0007, and a live pass in a question books it ────────
-- Free (and every plan after a silence) opens the live voice on the
-- question: the talk window comes first, then this pass - so the question
-- is booked here. A standby pass ("voice" window) books nothing: idle, it
-- costs nothing; the question it answers is booked when it opens.
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
    if v_kind = 'talk' then
        perform public._heytora_book(v_uid, 'talk', p_window);       -- 0011: once per question
    end if;
    return jsonb_build_object(
        'ok', true, 'window', p_window, 'plan', v_p ->> 'plan',
        'expires_at', extract(epoch from v_start + make_interval(mins => v_minutes)));
end;
$fn$;


-- ── heytora_voice_report: what Google billed for an answer's voice ───────────
-- Called by the server function (service role) with the price it worked out
-- itself from the app's usageMetadata. The question's cost becomes the
-- larger of its booking and its answers' total; a report never lowers it.
create or replace function public.heytora_voice_report(
    p_user   uuid,
    p_window bigint,
    p_micro  bigint,
    p_in     bigint default 0,
    p_out    bigint default 0,
    p_model  text   default ''
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_kind     text;
    v_at       timestamptz;
    v_reported bigint;
    v_charged  bigint;
    v_add      bigint;
begin
    -- One answer can't be worth more than $0.25, nor one question $5: a
    -- broken report must not empty someone's month.
    p_micro := least(greatest(coalesce(p_micro, 0), 0), 250000);
    perform pg_advisory_xact_lock(hashtext('voice-report:' || p_window::text));

    select e.kind, e.created_at into v_kind, v_at from public.usage_events e
     where e.id = p_window and e.user_id = p_user;
    if not found or v_kind <> 'talk' then
        return jsonb_build_object('ok', false, 'reason', 'window');
    end if;
    if v_at < now() - interval '3 hours' then
        return jsonb_build_object('ok', false, 'reason', 'late');
    end if;

    select coalesce(sum(c.reported_micro), 0), coalesce(sum(c.cost_micro), 0)
      into v_reported, v_charged
      from public.usage_costs c
     where c.window_id = p_window and c.user_id = p_user;
    v_reported := least(v_reported + p_micro, 5000000);
    v_add := greatest(v_reported - v_charged, 0);

    insert into public.usage_costs
        (user_id, kind, model, input_tokens, output_tokens, cost_micro, reported_micro, window_id)
    values (p_user, 'talk', left(coalesce(nullif(p_model, ''), 'live'), 80),
            greatest(coalesce(p_in, 0), 0), greatest(coalesce(p_out, 0), 0), v_add, p_micro, p_window);
    return jsonb_build_object('ok', true, 'reported', v_reported, 'added', v_add);
end;
$fn$;

revoke all on function public._heytora_spend(uuid, text, int, jsonb) from public, anon, authenticated;
revoke all on function public._heytora_live_open(uuid)                from public, anon, authenticated;
revoke all on function public._heytora_book(uuid, text, bigint, int)  from public, anon, authenticated;
revoke all on function public.heytora_pass(bigint, text)              from public, anon;
grant execute on function public.heytora_pass(bigint, text)           to authenticated;
revoke all on function public.heytora_voice_report(uuid, bigint, bigint, bigint, bigint, text)
    from public, anon, authenticated;
grant execute on function public.heytora_voice_report(uuid, bigint, bigint, bigint, bigint, text) to service_role;

commit;
