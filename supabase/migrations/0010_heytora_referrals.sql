-- =============================================================================
-- HeyTora — referrals: give 14 days of Pro, get a free month
-- Migration 0010  (needs 0009)       docs/REFERRALS.md is the plan this builds
--
-- A friend who signs up through a referrer's code gets 14 days of Pro (the
-- pro_trial plan: Pro's allowances, and Pro's fair use for 14 days - $6 of
-- its $12 a month). When the friend has asked Tora 15 questions (talk
-- messages, counted here already) within those 14 days, the referrer earns
-- a free month: applied to their next Paddle bill, banked until they pay.
--
-- The rules live here, where a modified app can't reach them: codes only
-- for an account's first 7 days, once; not your own, not from someone you
-- referred; not on the referrer's PC, and one trial per PC (a one-way device
-- hash the app sends); no throwaway email domains; at most 12 months a year,
-- and more than 3 in a calendar month flagged for the owners.
--
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

-- ── The friend's plan: Pro, for 14 days ───────────────────────────────────────
insert into public.plans (key, name, limits, sort_order, is_public)
select 'pro_trial', 'Pro trial', p.limits || '{"budget_usd": 6}'::jsonb, p.sort_order, false
  from public.plans p where p.key = 'pro'
on conflict (key) do update set name = excluded.name, limits = excluded.limits, is_public = false;

alter table public.profiles add column if not exists referral_code    text unique;
alter table public.profiles add column if not exists referred_by      uuid references auth.users(id) on delete set null;
alter table public.profiles add column if not exists trial_started_at timestamptz;
alter table public.profiles add column if not exists trial_until      timestamptz;
alter table public.profiles add column if not exists device_hash      text;
create index if not exists profiles_device_hash on public.profiles (device_hash);


-- ── Who referred whom, and what it earned ─────────────────────────────────────
create table if not exists public.referrals (
    id           bigserial primary key,
    referrer     uuid not null references auth.users(id) on delete cascade,
    friend       uuid not null unique references auth.users(id) on delete cascade,   -- once each
    code         text not null,
    status       text not null default 'joined' check (status in ('joined', 'qualified', 'withdrawn')),
    flagged      boolean not null default false,
    created_at   timestamptz not null default now(),
    qualified_at timestamptz
);
create index if not exists referrals_referrer on public.referrals (referrer);

-- One row per free month earned. A friend's account deleted takes its
-- referral, and the month with it (withdrawn).
create table if not exists public.reward_months (
    id          bigserial primary key,
    user_id     uuid not null references auth.users(id) on delete cascade,
    referral_id bigint not null unique references public.referrals(id) on delete cascade,
    earned_at   timestamptz not null default now(),
    used_at     timestamptz,
    used_on     text                       -- the Paddle subscription it was applied to
);
create index if not exists reward_months_user on public.reward_months (user_id);

-- Every PC that has had a free trial: one each.
create table if not exists public.trial_devices (
    device_hash text primary key,
    user_id     uuid references auth.users(id) on delete set null,
    created_at  timestamptz not null default now()
);

-- The website's note: "the sign-in with this PKCE challenge came from CODE"
-- (/login, no sign-in). Kept an hour; claimed by the app that holds the
-- challenge's verifier.
create table if not exists public.referral_intents (
    challenge  text primary key,
    code       text not null,
    created_at timestamptz not null default now()
);

create table if not exists public.disposable_domains (domain text primary key);
insert into public.disposable_domains (domain) values ('mailinator.com'), ('guerrillamail.com'), ('guerrillamail.net'), ('sharklasers.com'), ('10minutemail.com'), ('tempmail.com'), ('temp-mail.org'), ('yopmail.com'), ('trashmail.com'), ('getnada.com'), ('maildrop.cc'), ('dispostable.com'), ('fakeinbox.com'), ('throwawaymail.com'), ('emailondeck.com'), ('mintemail.com'), ('mohmal.com'), ('tempail.com'), ('burnermail.io'), ('spamgourmet.com')
on conflict do nothing;

alter table public.referrals          enable row level security;   -- no policies: the server only
alter table public.reward_months      enable row level security;
alter table public.trial_devices      enable row level security;
alter table public.referral_intents   enable row level security;
alter table public.disposable_domains enable row level security;
revoke all on public.referrals, public.reward_months, public.trial_devices, public.referral_intents,
              public.disposable_domains from anon, authenticated;


-- ── The plan, the period and the summary know the trial ───────────────────────
create or replace function public._heytora_plan(p_uid uuid)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_plan   text;
    v_status text;
    v_trial  timestamptz;
begin
    select p.plan, p.plan_status, p.trial_until into v_plan, v_status, v_trial
      from public.profiles p where p.id = p_uid;
    if not found then
        insert into public.profiles (id, email)
        values (p_uid, (select u.email from auth.users u where u.id = p_uid))
        on conflict (id) do nothing;
        v_plan := 'free';
        v_status := 'active';
    end if;
    if v_status not in ('active', 'trialing') then
        v_plan := 'free';
    end if;
    -- A friend's 14 days (0010): Pro, while it runs - unless they pay already.
    if v_plan = 'free' and v_trial is not null and v_trial > now() then
        v_plan := 'pro_trial';
    end if;
    return jsonb_build_object(
        'plan', v_plan,
        'limits', coalesce((select pl.limits from public.plans pl where pl.key = v_plan), '{}'::jsonb));
end;
$fn$;

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
    -- A friend's 14 days (0010): the trial is its own period, so its
    -- allowance is a whole Pro allowance for those 14 days.
    if found and v_prof.trial_until is not null and v_prof.trial_until > now()
       and v_prof.trial_started_at is not null and v_prof.trial_started_at <= now() then
        return jsonb_build_object('start', v_prof.trial_started_at, 'end', v_prof.trial_until,
                                  'billing', false, 'trial', true);
    end if;
    return jsonb_build_object('start', v_month, 'end', v_month + interval '1 month', 'billing', false);
end;
$fn$;

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
        'plan_cancel_at', v_prof.plan_cancel_at,
        'trial_until',    case when v_p ->> 'plan' = 'pro_trial' then v_prof.trial_until end,  -- 0010   -- a paid plan ending then (0008)
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


-- ── Codes ─────────────────────────────────────────────────────────────────────
-- Six letters of the email's name and four digits: DYLANH4821. Readable,
-- and unguessable enough that codes can't be walked.
create or replace function public._heytora_new_code(p_uid uuid)
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_base text;
    v_code text;
begin
    select upper(left(regexp_replace(split_part(coalesce(email, ''), '@', 1), '[^A-Za-z]', '', 'g'), 6))
      into v_base from public.profiles where id = p_uid;
    if coalesce(v_base, '') = '' then
        v_base := 'TORA';
    end if;
    loop
        v_code := v_base || lpad((floor(random() * 10000))::int::text, 4, '0');
        exit when not exists (select 1 from public.profiles where referral_code = v_code);
    end loop;
    update public.profiles set referral_code = v_code where id = p_uid;
    return v_code;
end;
$fn$;

-- First name only, for "Dylan gave you 14 days" - never the email.
create or replace function public._heytora_first_name(p_uid uuid)
returns text
language sql
stable
security definer
set search_path = public, pg_temp
as $fn$
    select nullif(split_part(trim(coalesce(display_name, '')), ' ', 1), '') from public.profiles where id = p_uid;
$fn$;

-- The website's /r/CODE page: does the code exist, and whose first name.
create or replace function public.heytora_referral_lookup(p_code text)
returns jsonb
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid uuid;
begin
    select id into v_uid from public.profiles where referral_code = upper(trim(p_code));
    if v_uid is null then
        return jsonb_build_object('ok', false);
    end if;
    return jsonb_build_object('ok', true, 'name', public._heytora_first_name(v_uid));
end;
$fn$;

-- /login's note, before a sign-in starts. Real codes only, an hour each,
-- and at most 60 notes an hour for one code.
create or replace function public.heytora_referral_intent(p_challenge text, p_code text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_code text := upper(trim(coalesce(p_code, '')));
begin
    delete from public.referral_intents where created_at < now() - interval '1 hour';
    if p_challenge !~ '^[A-Za-z0-9_-]{43}$' then
        return jsonb_build_object('ok', false, 'reason', 'challenge');
    end if;
    if not exists (select 1 from public.profiles where referral_code = v_code) then
        return jsonb_build_object('ok', false, 'reason', 'code');
    end if;
    if (select count(*) from public.referral_intents where code = v_code) >= 60 then
        return jsonb_build_object('ok', false, 'reason', 'busy');
    end if;
    insert into public.referral_intents (challenge, code) values (p_challenge, v_code)
    on conflict (challenge) do update set code = excluded.code, created_at = now();
    return jsonb_build_object('ok', true);
end;
$fn$;


-- ── Using a code (the Apply tab, or the website's note) ───────────────────────
create or replace function public.heytora_referral_apply(p_code text, p_device text, p_challenge text default null)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid      uuid := auth.uid();
    v_me       public.profiles%rowtype;
    v_referrer public.profiles%rowtype;
    v_code     text := upper(trim(coalesce(p_code, '')));
    v_device   text := nullif(trim(coalesce(p_device, '')), '');
    v_until    timestamptz;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    if p_challenge is not null then
        -- From the website: the note for this sign-in, if there is one.
        select code into v_code from public.referral_intents where challenge = p_challenge;
        if v_code is null then
            return jsonb_build_object('ok', false, 'reason', 'none');
        end if;
        delete from public.referral_intents where challenge = p_challenge;
    end if;
    perform public._heytora_plan(v_uid);                    -- makes the profile, if it's missing
    select * into v_me from public.profiles where id = v_uid;
    select * into v_referrer from public.profiles where referral_code = v_code;

    if v_referrer.id is null then
        return jsonb_build_object('ok', false, 'reason', 'unknown', 'message', 'That code isn''t valid - check it and try again.');
    end if;
    if v_referrer.id = v_uid then
        return jsonb_build_object('ok', false, 'reason', 'own', 'message', 'That''s your own code - share it with a friend instead.');
    end if;
    if exists (select 1 from public.referrals where friend = v_uid) then
        return jsonb_build_object('ok', false, 'reason', 'already', 'message', 'You''ve already used a code.');
    end if;
    if v_me.created_at < now() - interval '7 days' then
        return jsonb_build_object('ok', false, 'reason', 'too_old', 'message', 'Codes are for new accounts, in their first 7 days.');
    end if;
    if v_referrer.referred_by = v_uid then
        return jsonb_build_object('ok', false, 'reason', 'loop', 'message', 'You can''t use the code of someone you invited.');
    end if;
    if exists (select 1 from public.disposable_domains
                where domain = lower(split_part(coalesce(v_me.email, ''), '@', 2))) then
        return jsonb_build_object('ok', false, 'reason', 'email', 'message', 'Codes can''t be used with a throwaway email address.');
    end if;
    if v_me.plan <> 'free' and v_me.plan_status in ('active', 'trialing', 'past_due') then
        return jsonb_build_object('ok', false, 'reason', 'paying', 'message', 'You''re already on a paid plan.');
    end if;
    if v_device is null then
        return jsonb_build_object('ok', false, 'reason', 'device', 'message', 'Use the code in the HeyTora app on your PC.');
    end if;
    if v_device = v_referrer.device_hash then
        return jsonb_build_object('ok', false, 'reason', 'same_pc', 'message', 'A code can''t be used on the same PC as the person who shared it.');
    end if;
    if exists (select 1 from public.trial_devices where device_hash = v_device) then
        return jsonb_build_object('ok', false, 'reason', 'trial_used', 'message', 'This PC has already had a free trial.');
    end if;

    v_until := now() + interval '14 days';
    insert into public.referrals (referrer, friend, code) values (v_referrer.id, v_uid, v_code);
    insert into public.trial_devices (device_hash, user_id) values (v_device, v_uid);
    update public.profiles set referred_by = v_referrer.id, trial_started_at = now(), trial_until = v_until,
                               device_hash = v_device, updated_at = now()
     where id = v_uid;
    return jsonb_build_object('ok', true, 'referrer_name', public._heytora_first_name(v_referrer.id),
                              'trial_until', v_until);
end;
$fn$;


-- ── 15 questions within the 14 days: the referrer earns a month ───────────────
create or replace function public._heytora_referral_progress(p_friend uuid)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_r     public.referrals%rowtype;
    v_asked int;
begin
    select * into v_r from public.referrals where friend = p_friend and status = 'joined';
    if not found then
        return;
    end if;
    select count(*) into v_asked from public.usage_events e
     where e.user_id = p_friend and e.kind = 'talk'
       and e.created_at >= v_r.created_at and e.created_at < v_r.created_at + interval '14 days';
    if v_asked < 15 then
        return;
    end if;
    update public.referrals set status = 'qualified', qualified_at = now() where id = v_r.id;
    -- At most 12 months a year; past that the friend still counts, unrewarded.
    if (select count(*) from public.reward_months
         where user_id = v_r.referrer and earned_at > now() - interval '365 days') >= 12 then
        return;
    end if;
    insert into public.reward_months (user_id, referral_id) values (v_r.referrer, v_r.id)
    on conflict (referral_id) do nothing;
    -- More than 3 in a calendar month: flagged for the owners, not refused.
    if (select count(*) from public.reward_months
         where user_id = v_r.referrer
           and earned_at >= date_trunc('month', now() at time zone 'UTC') at time zone 'UTC') > 3 then
        update public.referrals set flagged = true where id = v_r.id;
    end if;
end;
$fn$;

create or replace function public._heytora_referral_on_usage()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
begin
    if new.kind = 'talk' and exists (select 1 from public.referrals where friend = new.user_id and status = 'joined') then
        begin
            perform public._heytora_referral_progress(new.user_id);
        exception when others then
            null;                                   -- a referral must never stop someone asking
        end;
    end if;
    return new;
end;
$fn$;

drop trigger if exists usage_events_referral_progress on public.usage_events;
create trigger usage_events_referral_progress
    after insert on public.usage_events
    for each row execute function public._heytora_referral_on_usage();


-- ── The Refer window: your code, your friends, your months ────────────────────
create or replace function public.heytora_referral_status(p_device text default null, p_name text default null)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
    v_uid  uuid := auth.uid();
    v_me   public.profiles%rowtype;
    v_code text;
begin
    if v_uid is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    perform public._heytora_plan(v_uid);
    -- The app says which PC and what to call you: the same-PC rule, and
    -- "Dylan gave you 14 days" on the website.
    update public.profiles set
        device_hash  = coalesce(nullif(trim(coalesce(p_device, '')), ''), device_hash),
        display_name = coalesce(nullif(trim(coalesce(p_name, '')), ''), display_name)
     where id = v_uid;
    select * into v_me from public.profiles where id = v_uid;
    v_code := coalesce(v_me.referral_code, public._heytora_new_code(v_uid));
    return jsonb_build_object(
        'code', v_code,
        'new_account', v_me.created_at > now() - interval '7 days'
                       and not exists (select 1 from public.referrals where friend = v_uid),
        'referred_by', case when v_me.referred_by is not null then public._heytora_first_name(v_me.referred_by) end,
        'trial_until', v_me.trial_until,
        'months_waiting', (select count(*) from public.reward_months m join public.referrals r on r.id = m.referral_id
                            where m.user_id = v_uid and m.used_at is null and r.status <> 'withdrawn'),
        'months_used', (select count(*) from public.reward_months where user_id = v_uid and used_at is not null),
        'friends', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'name', coalesce(public._heytora_first_name(r.friend),
                                        left(split_part(p.email, '@', 1), 1) || '•••@' || split_part(p.email, '@', 2)),
                       'status', r.status,
                       'joined_at', r.created_at,
                       'asked', least(15, (select count(*) from public.usage_events e
                                            where e.user_id = r.friend and e.kind = 'talk'
                                              and e.created_at >= r.created_at
                                              and e.created_at < r.created_at + interval '14 days')),
                       'earned', exists (select 1 from public.reward_months m where m.referral_id = r.id))
                   order by r.created_at desc)
              from public.referrals r join public.profiles p on p.id = r.friend
             where r.referrer = v_uid), '[]'::jsonb));
end;
$fn$;

-- A new code; the old one stops working for new sign-ups.
create or replace function public.heytora_referral_refresh()
returns text
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
begin
    if auth.uid() is null then
        raise exception 'not authenticated' using errcode = '28000';
    end if;
    perform public._heytora_plan(auth.uid());
    return public._heytora_new_code(auth.uid());
end;
$fn$;


-- ── Paying referrers: a month waiting goes on the next bill ───────────────────
create or replace function public.heytora_reward_next(p_uid uuid)
returns bigint
language sql
stable
security definer
set search_path = public, pg_temp
as $fn$
    select m.id from public.reward_months m join public.referrals r on r.id = m.referral_id
     where m.user_id = p_uid and m.used_at is null and r.status <> 'withdrawn'
     order by m.earned_at limit 1;
$fn$;

create or replace function public.heytora_reward_used(p_id bigint, p_on text)
returns void
language sql
security definer
set search_path = public, pg_temp
as $fn$
    update public.reward_months set used_at = now(), used_on = p_on where id = p_id and used_at is null;
$fn$;


-- ── Who may call what ─────────────────────────────────────────────────────────
revoke all on function public._heytora_new_code(uuid)                     from public, anon, authenticated;
revoke all on function public._heytora_first_name(uuid)                   from public, anon, authenticated;
revoke all on function public._heytora_referral_progress(uuid)            from public, anon, authenticated;
revoke all on function public._heytora_referral_on_usage()                from public, anon, authenticated;
revoke all on function public.heytora_referral_lookup(text)               from public, anon, authenticated;
revoke all on function public.heytora_referral_intent(text, text)         from public, anon, authenticated;
revoke all on function public.heytora_reward_next(uuid)                   from public, anon, authenticated;
revoke all on function public.heytora_reward_used(bigint, text)           from public, anon, authenticated;
grant execute on function public.heytora_referral_lookup(text)            to service_role;
grant execute on function public.heytora_referral_intent(text, text)      to service_role;
grant execute on function public.heytora_reward_next(uuid)                to service_role;
grant execute on function public.heytora_reward_used(bigint, text)        to service_role;
revoke all on function public.heytora_referral_apply(text, text, text)    from public, anon;
revoke all on function public.heytora_referral_status(text, text)         from public, anon;
revoke all on function public.heytora_referral_refresh()                  from public, anon;
grant execute on function public.heytora_referral_apply(text, text, text) to authenticated;
grant execute on function public.heytora_referral_status(text, text)      to authenticated;
grant execute on function public.heytora_referral_refresh()               to authenticated;
revoke all on function public._heytora_plan(uuid)                         from public, anon, authenticated;
revoke all on function public._heytora_period(uuid)                       from public, anon, authenticated;
revoke all on function public.get_usage_summary()                         from public, anon;
grant execute on function public.get_usage_summary()                      to authenticated;

commit;
