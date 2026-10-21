-- =============================================================================
-- HeyTora — a talk voice pass in one round trip
-- Migration 0013  (needs 0007; written as 0011, renumbered 21 Oct 2026 -
--                 0011 is the voice in the cost ceiling)        docs/SERVER_SPEED.md (AC-03)
--
-- A talk (or standby) voice pass asked the database twice, one after the
-- other - heytora_window, then heytora_pass - before Google was asked for
-- the token: two round trips in front of every voice session. This is the
-- two in one call, and one transaction: the same rules, the same results,
-- nothing new decided here. The function falls back to the two calls while
-- this migration is not applied, so either can be deployed first.
--
-- Returns {window: <heytora_window's answer>, pass: <heytora_pass's, or
-- null when the window was refused>}.
--
-- Idempotent: safe to run more than once.
-- =============================================================================

begin;

create or replace function public.heytora_talk_pass(
    p_kind  text,
    p_new   boolean default false,
    p_model text    default ''
)
returns jsonb
language plpgsql
security invoker                     -- the caller's own rights: the two it calls decide
set search_path = public, pg_temp
as $fn$
declare
    v_w jsonb;
    v_p jsonb;
begin
    v_w := public.heytora_window(p_kind, p_new);
    if not coalesce((v_w ->> 'ok')::boolean, false) then
        return jsonb_build_object('window', v_w, 'pass', null);
    end if;
    v_p := public.heytora_pass((v_w ->> 'window')::bigint, p_model);
    return jsonb_build_object('window', v_w, 'pass', v_p);
end;
$fn$;

revoke all on function public.heytora_talk_pass(text, boolean, text) from public, anon;
grant execute on function public.heytora_talk_pass(text, boolean, text) to authenticated;

commit;
