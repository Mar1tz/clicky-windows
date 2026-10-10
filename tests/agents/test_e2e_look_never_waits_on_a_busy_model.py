"""FAST-01 (docs/FAST_AND_RIGHT.md): a look never waits on a busy model.

10 Oct 2026: "what do you see on my screen?" took 85 s. gemini-3.8-flash, the
fast model, answered 4 of 8 identical looks with 503 (overloaded) and took
28-45 s over the rest, while gemini-3.6-flash and gemini-3.5-flash-lite
looked in 1-3 s. The fast look now races its backup model when it is slow
or fails, and skips a model that was overloaded for a while.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from ai import gemini_vision
from ai.gemini import Models
from ai.gemini_vision import Look


@pytest.fixture
def race(monkeypatch):
    """Two fake models; each one's delay (or None = fails) is set per test."""
    monkeypatch.setattr(Models, "VISION_FAST", "primary")
    monkeypatch.setattr(Models, "VISION_FAST_BACKUP", "backup")
    monkeypatch.setattr(gemini_vision, "HEDGE_AFTER_S", 0.2)
    monkeypatch.setattr(gemini_vision, "FAST_BUDGET_S", 1.0)
    monkeypatch.setattr(gemini_vision, "SLOW_S", 0.5)
    monkeypatch.setattr(gemini_vision, "_cool_until", {})
    delays: dict = {}
    asked: list = []

    async def fake_ask(question, image, *, deep, context, timeout, model=None, busy_retries=True, game=False):
        asked.append((model, busy_retries))
        delay = delays.get(model)
        if delay is None:
            return None
        await asyncio.sleep(delay)
        return Look(answer=f"from {model}", model=model)
    monkeypatch.setattr(gemini_vision, "_ask", fake_ask)
    return delays, asked


def _look():
    t = time.monotonic()
    out = asyncio.run(gemini_vision._fast("what's on my screen?", "img", context=""))
    return out, time.monotonic() - t


def test_a_quick_answer_never_asks_the_backup(race):
    delays, asked = race
    delays.update(primary=0.05, backup=0.05)
    out, took = _look()
    assert out.answer == "from primary" and took < 0.2
    assert asked == [("primary", False)], "and it never waits on busy retries either"


def test_a_slow_model_loses_to_the_backup_and_is_skipped_after(race):
    delays, asked = race
    delays.update(primary=5.0, backup=0.05)
    out, took = _look()
    assert out.answer == "from backup" and took < 0.5, "not the 5 s - the backup after 0.2 s"
    assert gemini_vision.fast_models()[0] == "backup", "the slow one waits its turn for a while"
    asked.clear()
    out, _ = _look()
    assert asked[0][0] == "backup" and out.answer == "from backup"


def test_a_failed_model_hands_over_at_once(race):
    delays, asked = race
    delays.update(primary=None, backup=0.05)
    out, took = _look()
    assert out.answer == "from backup" and took < 0.2, "no waiting for the race delay after a failure"


def test_when_both_are_down_it_gives_up_within_its_budget(race):
    delays, _asked = race
    delays.update(primary=5.0, backup=5.0)
    out, took = _look()
    assert out is None and took < 1.3


def test_a_slow_answer_still_counts_but_cools_its_model(race):
    delays, _asked = race
    delays.update(primary=0.6, backup=None)                  # the backup fails, the slow primary answers
    out, _took = _look()
    assert out.answer == "from primary"
    assert gemini_vision._cool_until.get("primary", 0) > time.monotonic()


def test_the_real_settings():
    assert gemini_vision.HEDGE_AFTER_S == 2.5 and gemini_vision.FAST_BUDGET_S == 15.0
    assert Models.VISION_FAST_BACKUP and Models.VISION_FAST_BACKUP != Models.VISION_FAST


def test_a_deep_look_gives_up_at_its_limit_however_google_stalls(monkeypatch):
    """10 Oct: a deep look hung 19 s on a 12 s limit - the HTTP timeout
    counts a silence, not the whole call - before the fast model answered."""
    from ai import gemini
    monkeypatch.setattr(gemini_vision, "_DEEP_TIMEOUT_S", 0.2)

    async def stalls(*_a, **_kw):
        await asyncio.sleep(5)
    monkeypatch.setattr(gemini, "generate_json", stalls)
    t = time.monotonic()
    out = asyncio.run(gemini_vision._ask("what's my best move?", "img", deep=True, context="", timeout=90))
    assert out is None and time.monotonic() - t < 1.0
