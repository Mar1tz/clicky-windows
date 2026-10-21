"""19 Oct 2026, the owner: "it's still very slow".

Every question that searched took 12-17 s to its first word: the free search
took 9-12 s and the voice spent 5-8 s more on a 4,000-character result. Now
Google Search through Gemini answers first (1.9-2.3 s, short and dated), the
free engines are asked together with a budget, and the voice gets a short
result.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from ai import web_search as ws


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(ws, "_google_refused", False)
    monkeypatch.setattr(ws.cfg, "search_provider", lambda: "duckduckgo")


def _answer(text, status=200):
    return SimpleNamespace(status_code=status, text=text, json=lambda: {
        "candidates": [{"content": {"parts": [{"text": text}]},
                        "groundingMetadata": {"groundingChunks": [{"web": {"title": "formula1.com"}}]}}]})


def test_google_answers_first_short_and_with_its_source(monkeypatch):
    from ai import gemini
    async def post(path, body, timeout, busy_retries=True):
        assert body["tools"] == [{"google_search": {}}]
        return _answer("Max Verstappen won the Singapore Grand Prix on 5 October 2026.")
    monkeypatch.setattr(gemini, "_post", post)
    monkeypatch.setattr(ws, "_quick", lambda q: pytest.fail("not needed"))
    out = asyncio.run(ws.search("who won the last grand prix"))
    assert out.startswith("Max Verstappen") and "Sources: formula1.com" in out


def test_a_server_that_refuses_google_is_not_asked_again(monkeypatch):
    from ai import gemini
    asked = []

    async def post(path, body, timeout, busy_retries=True):
        asked.append(1)
        return _answer('{"error":{"message":"Tool not forwarded: google_search"}}', status=400)

    async def quick(q):
        return "[1] Bitcoin - $82,425 (coinmarketcap.com)"
    monkeypatch.setattr(gemini, "_post", post)
    monkeypatch.setattr(ws, "_quick", quick)
    assert asyncio.run(ws.search("bitcoin price")).startswith("[1] Bitcoin")
    asyncio.run(ws.search("bitcoin price"))
    assert asked == [1], "refused once: the free engines from then on"


def test_the_free_engines_race_and_the_first_with_results_wins(monkeypatch):
    import ddgs

    class DDGS:
        def __init__(self, timeout=None):
            pass

        def text(self, query, max_results=5, region="", backend=""):
            if backend == "bing":
                time.sleep(0.3)
                return [{"title": "Cape Town weather", "body": "Cloudy, 18 C", "href": "https://w.example"}]
            if backend == "yahoo":
                time.sleep(3)
                return [{"title": "late", "body": "late", "href": "x"}]
            return []
    monkeypatch.setattr(ddgs, "DDGS", DDGS)
    async def timed():
        started = time.monotonic()
        out = await ws._quick("weather in cape town")
        return out, time.monotonic() - started          # not asyncio.run's wait for the loser's thread
    out, took = asyncio.run(timed())
    assert out.startswith("[1] Cape Town weather - Cloudy, 18 C") and took < 1.5, took


def test_nothing_anywhere_ends_within_its_budgets(monkeypatch):
    from ai import gemini

    async def post(path, body, timeout, busy_retries=True):
        return _answer("", status=500)

    async def quick(q):
        return ""

    async def deep(q, n):
        await asyncio.sleep(10)
        return "too late"
    monkeypatch.setattr(gemini, "_post", post)
    monkeypatch.setattr(ws, "_quick", quick)
    monkeypatch.setattr(ws, "_free_deep_search", deep)
    monkeypatch.setattr(ws, "DEEP_BUDGET_S", 0.2)
    started = time.monotonic()
    assert asyncio.run(ws.search("anything")) == "" and time.monotonic() - started < 1.0
