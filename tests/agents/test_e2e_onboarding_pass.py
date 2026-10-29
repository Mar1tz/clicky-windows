"""The first launch's voice is free (migration 0014, 29 Oct 2026).

The new onboarding talks - it hears your name and asks four questions out
loud - and as talk messages that would use much of Free's 50 credits before
anyone has asked Tora anything. Its voice lives in an "onboarding" window
instead: never a talk message, never on the credits, three per account
ever. The server decides that; the app only asks, and falls back to a
normal pass when the server says the tour's voice is used up.

The database's side by reading the migration (it was also run against the
local Supabase: three tours then onboarding_used, six passes a window,
nothing in usage_costs); the app's against the fake server.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from tests.agents.test_e2e_heytora_account import _signed_in, heytora  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "supabase/migrations/0014_heytora_onboarding_pass.sql").read_text(encoding="utf-8")
COST = (ROOT / "supabase/migrations/0011_heytora_voice_cost.sql").read_text(encoding="utf-8")
CREDITS = (ROOT / "supabase/migrations/0012_heytora_credits.sql").read_text(encoding="utf-8")
FN = (ROOT / "supabase/functions/heytora/index.ts").read_text(encoding="utf-8")
MODEL = "gemini-live"


def _passes(h):
    return [b for _m, p, _h, b in h.fake.calls if p.endswith("/voice-pass")]


@pytest.fixture
def tour_voice():
    """Inside the tour's own voice (TOUR_VOICE), as the interview runs."""
    from account.service import TOUR_VOICE
    token = TOUR_VOICE.set(True)
    yield
    TOUR_VOICE.reset(token)


# -- the database ------------------------------------------------------------------------------------------

def test_every_plan_has_a_few_free_tours_ever():
    assert "update public.plans set limits = limits" in SQL and "where key" not in SQL.split("alter table")[0]
    assert '"onboarding_ever": 3' in SQL and '"onboarding": null' in SQL
    assert "'onboarding'));" in SQL, "a kind usage_events accepts"
    tour = SQL.split("if p_kind = 'onboarding' then")[1].split("end if;\n    end if;")[0]
    assert "e.kind = 'onboarding'" in tour and "created_at" not in tour, "ever - not this month"
    assert "'onboarding_used'" in tour


def test_it_is_never_on_the_credits():
    spend = CREDITS.split("create or replace function public._heytora_spend")[1]
    assert "p_kind in ('dictation', 'wake') or (p_kind = 'talk'" in spend, "only these are booked"
    report = COST.split("create or replace function public.heytora_voice_report")[1]
    assert "v_kind <> 'talk'" in report, "a voice report on an onboarding window is refused"
    passes = SQL.split("create or replace function public.heytora_pass")[1]
    assert "if v_kind = 'talk' then\n        perform public._heytora_book" in passes


def test_a_tour_window_has_its_own_passes():
    passes = SQL.split("create or replace function public.heytora_pass")[1]
    assert "e.kind in ('talk', 'voice', 'onboarding')" in passes
    assert "(v_kind || '_passes')" in passes and '"onboarding_passes": 6' in SQL


def test_the_server_gives_no_ticket_and_says_when_it_is_used_up():
    route = FN.split('if (path === "voice-pass"')[1].split('if (path === "voice-usage"')[0]
    assert 'body?.onboarding === true' in route and 'p_kind: "onboarding"' in route
    assert "if (standby || onboarding)" in route, "returned before a ticket is made"
    assert 'case "onboarding_used":' in FN


# -- the app -----------------------------------------------------------------------------------------------

def test_the_tour_asks_for_free_passes_and_counts_nothing(heytora, tour_voice):
    _signed_in(heytora)
    before = dict(heytora.account.summary().get("usage") or {})
    heytora.account.onboarding(True)
    heytora.account.new_talk()                         # her hello to the name step
    first = heytora.account.voice_pass("talk", MODEL)
    heytora.account.voice_pass("talk", MODEL)          # a reconnect: the same tour window
    assert _passes(heytora) == [{"kind": "talk", "model": MODEL, "onboarding": True, "new": True},
                                {"kind": "talk", "model": MODEL, "onboarding": True, "new": False}]
    assert first.ticket == "" and heytora.fake.opened["talk"] == 0, "no talk message"
    assert not heytora.account.talk_pending(), "that question was the tour's"
    assert dict(heytora.account.summary().get("usage") or {}) == before


def test_used_up_it_is_talk_as_usual(heytora, tour_voice):
    _signed_in(heytora)
    heytora.fake.onboarding_left = 0
    heytora.account.onboarding(True)
    heytora.account.new_talk()
    vp = heytora.account.voice_pass("talk", MODEL)
    assert vp.ticket and heytora.fake.opened["talk"] == 1
    assert not heytora.account.onboarding_on(), "not asked again"
    assert _passes(heytora)[-1] == {"kind": "talk", "model": MODEL, "new": True}


def test_after_the_tour_the_voice_is_counted_again(heytora, tour_voice):
    _signed_in(heytora)
    heytora.account.onboarding(True)
    heytora.account.voice_pass("talk", MODEL)
    heytora.account.onboarding(False)
    heytora.account.new_talk()
    heytora.account.voice_pass("talk", MODEL)
    assert "onboarding" not in _passes(heytora)[-1] and heytora.fake.opened["talk"] == 1


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_apps_own_voice_beside_the_tour_is_not_free(heytora):
    _signed_in(heytora)
    heytora.account.onboarding(True)
    heytora.account.new_talk()
    heytora.account.voice_pass("talk", MODEL)          # not inside the tour's voice
    assert "onboarding" not in _passes(heytora)[-1] and heytora.fake.opened["talk"] == 1
    assert heytora.account.onboarding_on(), "still there for the tour"


def test_the_panel_turns_it_on_and_off(heytora, qapp, monkeypatch):
    import ui.onboarding_shape as O
    monkeypatch.setattr(O, "animations_on", lambda: False)
    _signed_in(heytora)
    p = O.OnboardingPanel(O.stand_ins())
    p.open_from()
    assert heytora.account.onboarding_on()
    p.skip()
    assert not heytora.account.onboarding_on()
    p.hide()
    p.deleteLater()


def test_signed_out_the_panel_leaves_the_account_alone(heytora):
    from ui.onboarding_shape import free_voice
    free_voice(True)
    assert not heytora.account.onboarding_on()
