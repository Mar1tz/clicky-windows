"""FS-14 (docs/FAST_AND_RIGHT.md): when HeyTora's own credits run out.

9 Oct 2026, 13:45 to about 14:20: Google refused HeyTora's business account -
"Your prepayment credits are depleted. Please go to AI Studio..." - and every
plan user got no answers for about 35 minutes, while being told to top up
Gemini credits in AI Studio: credits they do not have.
"""
from __future__ import annotations

import os

import pytest

from ai import gemini

DEPLETED = ("received 1011 (internal error) Your prepayment credits are depleted. Please go to AI Studio at "
            "https://ai.studio/projects to manage your project and billing")


@pytest.mark.parametrize("message, own", [
    (DEPLETED, "billing"),
    ("API key not valid. Please pass a valid API key.", "key"),
    ("Quota exceeded for metric: generate_requests_per_model_per_day", "quota"),
])
def test_on_a_plan_googles_refusal_is_heytoras_side(monkeypatch, message, own):
    monkeypatch.setattr(gemini, "own_key", lambda: False)
    assert gemini.problem_of(message) == "service"
    monkeypatch.setattr(gemini, "own_key", lambda: True)
    assert gemini.problem_of(message) == own, "an own key's problem is still the user's to fix"


def test_the_plans_own_refusals_are_unchanged(monkeypatch):
    monkeypatch.setattr(gemini, "own_key", lambda: False)
    assert gemini.problem_of("HEYTORA_LIMIT: this month's talk is used up") == "plan"
    assert gemini.problem_of("overloaded, try again later") == "busy"


def test_what_a_plan_user_is_told(monkeypatch):
    import companion_manager as cm
    words = cm.CompanionManager._PROBLEM_WORDS["service"]
    assert "not you" in words and "top up" in words.lower() and "ai.studio" not in words
    assert "nothing to top up" in words


def test_the_tray_says_it_is_heytoras_side(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    import inspect

    from ui import tray
    src = inspect.getsource(tray)
    assert '"service"' in src and "HeyTora is having trouble on our side" in src
