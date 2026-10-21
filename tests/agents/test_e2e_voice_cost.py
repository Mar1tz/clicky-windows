"""The voice inside the cost ceiling (migration 0011, 21 Oct 2026).

The live voice, dictation and the wake word go straight from the app to
Google, so the plan's monthly cost ceiling never saw them: a Pro user on the
daily limit could cost ~$90 a month on $20. Now each use is booked as it
opens, the app reports what Google billed for every answer, the server adds
what is above the booking, and the ceiling is checked before anything opens.

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
    spend = SQL[SQL.index("function public._heytora_spend("):SQL.index("function public.heytora_voice_report(")]
    assert "from public.voice_estimates v where v.kind = p_kind" in spend
    assert "values (p_uid, p_kind, 'estimate'" in spend and "v_id);" in spend
    for line in ("('talk',      0.010,", "('dictation', 0.005,", "('wake',      0.0005,"):
        assert line in SQL, line
    assert "on conflict (kind) do nothing" in SQL, "edited estimates are kept"


def test_a_report_only_ever_adds():
    rep = SQL[SQL.index("function public.heytora_voice_report("):]
    assert "v_add := greatest(v_total - v_booked, 0);" in rep
    assert "p_micro := least(greatest(coalesce(p_micro, 0), 0), 250000);" in rep, "one answer: at most $0.25"
    assert "least(public.voice_reports.reported_micro + excluded.reported_micro, 5000000)" in rep
    assert "e.id = p_window and e.user_id = p_user" in rep, "only your own question"
    assert "v_kind <> 'talk'" in rep and "interval '3 hours'" in rep


def test_only_the_server_function_may_report():
    assert "from public, anon, authenticated;" in SQL
    assert "grant execute on function public.heytora_voice_report(uuid, bigint, bigint, bigint, bigint, text) " \
           "to service_role" in SQL


def test_the_ceilings_are_half_of_what_each_plan_leaves():
    assert "'{\"budget_usd\": 9}'::jsonb  where key = 'pro'" in SQL
    assert "'{\"budget_usd\": 47}'::jsonb where key = 'max'" in SQL
