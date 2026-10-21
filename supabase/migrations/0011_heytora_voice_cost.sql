-- =============================================================================
-- HeyTora — the voice inside the cost ceiling
-- Migration 0011  (needs 0004, 0006, 0007, 0009, 0010)
--
-- The live voice, dictation and the wake word go straight from the app to
-- Google, so the server never saw what they cost: budget_usd held only the
-- forwarded calls (looks, agents), and the voice was limited by counts alone.
-- On 21 Oct a spoken answer cost $0.0099 on average (tools/voice_eval.py,
-- 15 questions) - on Pro's daily limit, ~$90 a month against $20.
--
--   * Each use is BOOKED when it opens, at voice_estimates' price: a question
--     (talk window), a dictation, a wake-word check. The server does it, so a
--     modified app can't skip it.
--   * After each answer the app sends Google's own usageMetadata; the server
--     prices it (rules.ts liveCostMicro) and heytora_voice_report adds only
--     what is ABOVE what the question already booked - never less.
--   * The ceiling is checked before ANY use opens, not only forwarded calls:
--     at the ceiling no new question, dictation, agent job or standby voice
--     starts.
--   * budget_usd set for a 50% margin (the owners, 21 Oct 2026): Pro $9 and
--     Max $47 of the $18.50 / $94.50 left after Paddle's fee.
--
-- Idempotent: safe to run more than once. Edited estimates are kept.
-- =============================================================================

begin;

-- ── What a use is booked at, before Google's real bill ───────────────────────
create table if not exists public.voice_estimates (
    kind text    primary key,
    usd  numeric not null check (usd >= 0),
    note text    not null default ''
);
alter table public.voice_estimates enable row level security;   -- no policies: the server only

insert into public.voice_estimates (kind, usd, note) values
    ('talk',      0.010,  'one spoken answer: $0.0099 average on 21 Oct (voice_eval); more comes from the report'),
    ('dictation', 0.005,  'about 30 s of speech to text; not reported by the app yet'),
    ('wake',      0.0005, 'one wake-word check; not reported by the app yet')
on conflict (kind) do nothing;


-- ── Which question a cost belongs to ─────────────────────────────────────────
alter table public.usage_costs add column if not exists window_id bigint;
create index if not exists usage_costs_window_idx on public.usage_costs (window_id) where window_id is not null;

-- What the app reported for each question's voice, summed.
create table if not exists public.voice_reports (
    window_id      bigint      primary key,
    user_id        uuid        not null references auth.users(id) on delete cascade,
    reported_micro bigint      not null default 0 check (reported_micro >= 0),
    updated_at     timestamptz not null default now()
);
alter table public.voice_reports enable row level security;      -- no policies: the server only


-- ── The ceilings: 50% of what each plan leaves after Paddle ──────────────────
update public.plans set limits = limits || '{"budget_usd": 9}'::jsonb  where key = 'pro';
update public.plans set limits = limits || '{"budget_usd": 47}'::jsonb where key = 'max';


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
    v_est     numeric;
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

    -- 0011: a use that goes straight to Google is booked as it opens.
    select v.usd into v_est from public.voice_estimates v where v.kind = p_kind;
    if coalesce(v_est, 0) > 0 then
        insert into public.usage_costs (user_id, kind, model, cost_micro, window_id)
        values (p_uid, p_kind, 'estimate', ceil(v_est * p_quantity * 1000000)::bigint, v_id);
    end if;

    return jsonb_build_object(
        'ok', true, 'id', v_id, 'kind', p_kind, 'used', v_used + p_quantity, 'plan', v_plan,
        'limit', case when jsonb_typeof(v_limit) = 'number' then (v_limit #>> '{}')::int end,
        'limits', v_limits);
end;
$fn$;


-- ── heytora_voice_report: what Google billed for an answer's voice ───────────
-- Called by the server function (service role) with the cost it worked out
-- itself from the app's usageMetadata. Adds only what the question's reports
-- come to above what it already booked; a report can never lower a cost.
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
    v_kind    text;
    v_at      timestamptz;
    v_total   bigint;
    v_booked  bigint;
    v_add     bigint;
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

    insert into public.voice_reports (window_id, user_id, reported_micro)
    values (p_window, p_user, p_micro)
    on conflict (window_id) do update
        set reported_micro = least(public.voice_reports.reported_micro + excluded.reported_micro, 5000000),
            updated_at = now()
    returning reported_micro into v_total;

    select coalesce(sum(c.cost_micro), 0) into v_booked from public.usage_costs c
     where c.window_id = p_window and c.user_id = p_user;
    v_add := greatest(v_total - v_booked, 0);
    if v_add > 0 then
        insert into public.usage_costs (user_id, kind, model, input_tokens, output_tokens, cost_micro, window_id)
        values (p_user, 'talk', left(coalesce(nullif(p_model, ''), 'live'), 80),
                greatest(coalesce(p_in, 0), 0), greatest(coalesce(p_out, 0), 0), v_add, p_window);
    end if;
    return jsonb_build_object('ok', true, 'reported', v_total, 'added', v_add);
end;
$fn$;

revoke all on function public._heytora_spend(uuid, text, int, jsonb) from public, anon, authenticated;
revoke all on function public.heytora_voice_report(uuid, bigint, bigint, bigint, bigint, text)
    from public, anon, authenticated;
grant execute on function public.heytora_voice_report(uuid, bigint, bigint, bigint, bigint, text) to service_role;

commit;
