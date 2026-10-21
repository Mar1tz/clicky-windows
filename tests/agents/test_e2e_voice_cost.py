"""The voice inside the cost ceiling (migration 0011, 21 Oct 2026).

The live voice, dictation and the wake word go straight from the app to
Google, so the plan's monthly cost ceiling never saw them: a Pro user on the
daily limit could cost ~$90 a month on $20. Google bills per answer, not per
minute, and every question opens a talk window - so each question is booked
as it opens, the app reports what Google billed for every answer, the server
adds what is above the booking, and the ceiling is checked before anything
opens.

The app's side is tested against the fake server; the server's pricing with
Node (heytora_rules.test.ts, run by test_e2e_heytora_metering); the
database's by reading the migration.
"""
from __future__ import annotations

import time
from pathlib import Path

from tests.agents.test_e2e_heytora_account import _signed_in, heytora  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "supabase/migrations/0011_heytora_voice_cost.sql").read_text(encoding="utf-8")
FN = (ROOT / "supabase/functions/heytora/index.ts").read_text(encoding="utf-8")
USAGE = {"promptTokenCount": 4000, "responseTokenCount": 120,
         "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 4000}]}


def _reports(h, n=1, wait=2.0):
    """The report goes in the background: wait for it."""
    end = time.time() + wait
    while len(h.fake.voice_reports) < n and time.time() < end:
        time.sleep(0.01)
    return h.fake.voice_reports


# -- the app ----------------------------------------------------------------------------------------------

def test_an_answers_voice_is_reported_against_its_question(heytora):
    _signed_in(heytora)
    heytora.account.new_talk()
    ticket = heytora.account.ticket("talk")
    window = int(ticket.split(".")[1])
    heytora.account.report_voice(USAGE)
    assert _reports(heytora) == [{"window": window, "usage": USAGE}]


def test_nothing_is_reported_without_a_question_or_a_bill(heytora):
    _signed_in(heytora)
    heytora.account.report_voice(USAGE)          # no talk window yet
    heytora.account.new_talk()
    heytora.account.ticket("talk")
    heytora.account.report_voice({})             # nothing billed
    heytora.account.report_voice(None)
    assert _reports(heytora, wait=0.3) == []


def test_a_failed_report_never_reaches_the_answer(heytora, monkeypatch):
    _signed_in(heytora)
    heytora.account.new_talk()
    heytora.account.ticket("talk")
    monkeypatch.setattr(heytora.account, "_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    heytora.account.report_voice(USAGE)          # must not raise
    time.sleep(0.1)


def test_the_companion_reports_on_the_plan_and_not_on_an_own_key(monkeypatch):
    import companion_manager
    from account import service
    sent = []

    class Fake:
        def report_voice(self, usage):
            sent.append(usage)

    monkeypatch.setattr(service, "account", lambda: Fake())
    cm = companion_manager.CompanionManager.__new__(companion_manager.CompanionManager)
    cm._ans_stats, cm._ans = None, None

    monkeypatch.setattr(companion_manager.gemini, "own_key", lambda: False)
    cm._note_voice(USAGE)
    monkeypatch.setattr(companion_manager.gemini, "own_key", lambda: True)
    cm._note_voice(USAGE)
    assert sent == [USAGE], "an own key is the user's bill, not the plan's"


# -- the server -------------------------------------------------------------------------------------------

def test_the_server_prices_the_report_itself():
    i = FN.index('path === "voice-usage"')
    block = FN[i:FN.index("omniroute/v1/models", i)]
    assert "liveCostMicro(body?.usage)" in block, "the price is the server's, never the app's"
    assert "heytora_voice_report" in block and "p_user: user.id" in block
    assert "createClient(SUPABASE_URL, SERVICE_KEY" in block


# -- the database -----------------------------------------------------------------------------------------

def test_every_kind_is_held_by_the_ceiling():
    spend = SQL[SQL.index("function public._heytora_spend("):SQL.index("function public.heytora_voice_report(")]
    assert "if jsonb_typeof(v_limits -> 'budget_usd') = 'number' then" in spend
    assert "p_kind = 'calls' and" not in spend, "0009 only held forwarded calls"
    # the ceiling is checked before the use is counted
    assert spend.index("'reason', 'budget'") < spend.index("insert into public.usage_events")


def test_a_direct_use_is_booked_as_it_opens():
    book = SQL[SQL.index("function public._heytora_book("):SQL.index("function public._heytora_spend(")]
    assert "from public.direct_cost_estimates d where d.kind = p_kind" in book
    assert "c.model = 'estimate'" in book and "return;" in book, "once per question, however it opens"
    spend = SQL[SQL.index("function public._heytora_spend("):SQL.index("function public.heytora_pass(")]
    assert "perform public._heytora_book(p_uid, p_kind, v_id, p_quantity);" in spend
    # a question at the long-session average, measured 21 Oct ($0.0147)
    for line in ("('talk',      0.015,", "('dictation', 0.005,", "('wake',      0.0005,"):
        assert line in SQL, line
    assert "on conflict (kind) do nothing" in SQL, "edited estimates are kept"


def test_only_a_question_the_live_voice_answers_is_booked():
    """The cascade (transcribe -> Gemini -> TTS) goes through this server and
    is priced call by call already; booking it as well would charge it twice."""
    spend = SQL[SQL.index("function public._heytora_spend("):SQL.index("function public.heytora_pass(")]
    assert "p_kind in ('dictation', 'wake') or (p_kind = 'talk' and public._heytora_live_open(p_uid))" in spend
    live = SQL[SQL.index("function public._heytora_live_open("):SQL.index("function public._heytora_book(")]
    assert "p.kind = 'pass'" in live and "w.kind in ('talk', 'voice')" in live, \
        "talk and standby passes are the live voice; the cascade's transcriber is a dictation"
    assert "> now())" in live, "only a pass whose window hasn't ended"
    pass_ = SQL[SQL.index("function public.heytora_pass("):SQL.index("function public.heytora_voice_report(")]
    assert "if v_kind = 'talk' then\n        perform public._heytora_book(v_uid, 'talk', p_window);" in pass_, \
        "a live pass opened for a question books it (Free, or after a silence)"
    assert "grant execute on function public.heytora_pass(bigint, text)           to authenticated;" in SQL


def test_a_report_only_ever_adds():
    rep = SQL[SQL.index("function public.heytora_voice_report("):]
    # the question's cost becomes max(booking, answers' total): one ledger
    assert "v_reported := least(v_reported + p_micro, 5000000);" in rep, "one question: at most $5"
    assert "v_add := greatest(v_reported - v_charged, 0);" in rep
    assert "p_micro := least(greatest(coalesce(p_micro, 0), 0), 250000);" in rep, "one answer: at most $0.25"
    assert "e.id = p_window and e.user_id = p_user" in rep, "only your own question"
    assert "v_kind <> 'talk'" in rep and "interval '3 hours'" in rep
    assert "voice_reports" not in SQL, "the report is a row of usage_costs, not a table of its own"


def _cost_of_a_question(booking: int, answers: list[int]) -> int:
    """heytora_voice_report's arithmetic, row by row, as the ledger sums it."""
    charged, reported = booking, 0
    for micro in answers:
        reported = min(reported + min(max(micro, 0), 250_000), 5_000_000)
        charged += max(reported - charged, 0)
    return charged


def test_what_a_question_ends_up_costing():
    assert _cost_of_a_question(15_000, []) == 15_000, "no report (an older app): the booking"
    assert _cost_of_a_question(15_000, [9_000]) == 15_000, "a cheap answer never lowers it"
    assert _cost_of_a_question(15_000, [9_000, 9_000]) == 18_000, "two answers past the booking"
    assert _cost_of_a_question(15_000, [25_200]) == 25_200, "a dear answer, in full"
    assert _cost_of_a_question(15_000, [10**9] * 30) == 5_000_000, "a broken report stops at $5"


def test_only_the_server_function_may_report():
    assert "from public, anon, authenticated;" in SQL
    assert "grant execute on function public.heytora_voice_report(uuid, bigint, bigint, bigint, bigint, text) " \
           "to service_role" in SQL


def test_the_ceilings_are_half_of_what_each_plan_leaves():
    assert "'{\"budget_usd\": 9}'::jsonb  where key = 'pro'" in SQL
    assert "'{\"budget_usd\": 47}'::jsonb where key = 'max'" in SQL


# -- what a refusal says (21 Oct 2026) ----------------------------------------------------------------------

def test_a_refusal_says_when_and_where_more_comes_from():
    """The right day (a paid plan's month starts on its renewal day), more in
    the app's Plans & billing (the website sells nothing), nothing above Max,
    and no "use your own key" to someone who pays."""
    from account.service import limit_words
    on_6_nov = "2026-11-06T10:00:00+00:00"
    pro = limit_words({"reason": "budget", "plan": "pro", "kind": "calls", "resets_at": on_6_nov})
    assert pro == ("You've used this month's credits on the Pro plan. They come back on 6 Nov. "
                   "Add credits or move to Max in Settings → Plans & billing.")
    top = limit_words({"reason": "budget", "plan": "max", "kind": "talk", "resets_at": on_6_nov})
    assert top == ("You've used this month's credits on the Max plan. They come back on 6 Nov. "
                   "Add credits in Settings → Plans & billing."), "nothing above Max - but credits"
    free = limit_words({"reason": "budget", "plan": "free", "kind": "talk"})
    assert free == ("You've used this month's free credits. They come back next month. "
                    "Upgrade in Settings → Plans & billing."), "credits are for paid plans"
    trial = limit_words({"reason": "limit", "plan": "pro_trial", "kind": "agent", "limit": 150,
                         "resets_at": on_6_nov})
    assert trial == ("You've used all 150 agent messages on the Pro trial plan this month. They come back "
                     "on 6 Nov. Choose a plan in Settings → Plans & billing.")
    for words in (pro, top, free, trial):
        assert "own Gemini key" not in words and "website" not in words and "the 1st" not in words


def test_the_server_says_it_in_the_same_words():
    for phrase in ('" Upgrade in Settings → Plans & billing."', '" Choose a plan in Settings → Plans & billing."',
                   '" Or move to Max in Settings → Plans & billing."', "They come back ${when}.${more}",
                   '" Add credits or move to Max in Settings → Plans & billing."',
                   '" Add credits in Settings → Plans & billing."', 'pro_trial: "Pro trial"'):
        assert phrase in FN, phrase
    assert "own Gemini key" not in FN and "resets on the 1st" not in FN
    assert FN[FN.index("Deno.serve("):].count("limitResponse(") == 1, \
        "every refusal goes through refused(), which alone calls it"
    assert 'resets_at: (await rpc("get_usage_summary", {})).period_end' in FN
