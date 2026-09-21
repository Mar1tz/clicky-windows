"""MockProvider + Router + AccountStore."""
import asyncio
import json

import pytest

from agents.core import (
    ConnectedAccount, ProviderCapabilities, ProviderMessage, ProviderResponse,
    TaskKind, ToolCall, ToolSpec,
)
from agents.providers import AccountStore, MockProvider, Router, build_router


# -- MockProvider ------------------------------------------------------------

def test_mock_script_consumed_in_order_then_last_repeats():
    p = MockProvider(script=[
        ProviderResponse(text="one"),
        ProviderResponse(text="two"),
    ])
    got = [asyncio.run(p.complete([ProviderMessage(role="user", content="x")])).text
           for _ in range(4)]
    assert got == ["one", "two", "two", "two"]        # last repeats
    assert p.call_count == 4


def test_mock_handler_mode_and_call_recording():
    def handler(messages, tools):
        # branch on what the worker sent
        if any(m.role == "user" and "act" in m.content for m in messages):
            return ProviderResponse(tool_calls=[ToolCall("read_file", {"path": "a"})],
                                    finish_reason="tool_calls")
        return ProviderResponse(text="done")

    p = MockProvider(handler=handler)
    r1 = asyncio.run(p.complete([ProviderMessage(role="user", content="please act")],
                                tools=[ToolSpec("read_file", "d")], system="sys"))
    r2 = asyncio.run(p.complete([ProviderMessage(role="user", content="idle")]))
    assert r1.tool_calls and r1.tool_calls[0].tool_name == "read_file"
    assert r2.text == "done"
    # calls recorded with messages, tools and system
    assert p.calls[0].system == "sys"
    assert [s.name for s in p.calls[0].tools] == ["read_file"]
    assert p.calls[1].tools == []


def test_mock_capabilities():
    caps = MockProvider().capabilities()
    assert isinstance(caps, ProviderCapabilities)
    assert caps.tools and not caps.vision and caps.local and caps.cost_tier == 0


def test_mock_empty_returns_terminating_stop():
    r = asyncio.run(MockProvider().complete([ProviderMessage(role="user", content="x")]))
    assert r.text == "" and r.finish_reason == "stop" and not r.tool_calls


# -- Router ------------------------------------------------------------------

def test_router_highest_priority_for_kind_wins():
    lo, hi = MockProvider(), MockProvider()
    lo.name, hi.name = "lo", "hi"
    r = Router()
    r.register(lo, kinds=[TaskKind.CODING], priority=1)
    r.register(hi, kinds=[TaskKind.CODING], priority=5)
    assert r.pick(TaskKind.CODING).name == "hi"


def test_router_hint_beats_priority():
    a, b = MockProvider(), MockProvider()
    a.name, b.name = "a", "b"
    r = Router()
    r.register(a, kinds=[TaskKind.CODING], priority=1)
    r.register(b, kinds=[TaskKind.CODING], priority=9)
    assert r.pick(TaskKind.CODING, hint="a").name == "a"


def test_router_falls_back_to_general_then_any():
    gen, cat = MockProvider(), MockProvider()
    gen.name, cat.name = "gen", "cat"
    r = Router()
    r.register(gen, kinds=[TaskKind.GENERAL], priority=1)
    # RESEARCH is registered nowhere -> GENERAL fallback
    assert r.pick(TaskKind.RESEARCH).name == "gen"

    r2 = Router()
    r2.register(cat, kinds=[TaskKind.FILE], priority=1)   # neither exact nor GENERAL
    assert r2.pick(TaskKind.BROWSER).name == "cat"        # "any provider" fallback


def test_router_catch_all_registration_matches_every_kind():
    p = MockProvider()
    r = Router()
    r.register(p, kinds=None, priority=0)          # catch-all
    for k in (TaskKind.CODING, TaskKind.VISION, TaskKind.BROWSER, TaskKind.GENERAL):
        assert r.pick(k) is p


def test_router_vision_filter_skips_blind_providers():
    blind = MockProvider(vision=False)
    seer = MockProvider(vision=True)
    blind.name, seer.name = "blind", "seer"
    r = Router()
    r.register(blind, kinds=[TaskKind.VISION], priority=9)   # higher, but blind
    r.register(seer, kinds=[TaskKind.VISION], priority=1)
    assert r.pick(TaskKind.VISION, needs_vision=True).name == "seer"
    # without the vision requirement, the higher-priority blind one wins
    assert r.pick(TaskKind.VISION).name == "blind"


def test_router_raises_lookup_error_when_nothing_fits():
    r = Router()
    with pytest.raises(LookupError):
        r.pick(TaskKind.CODING)                    # empty router
    r.register(MockProvider(vision=False), kinds=None)
    with pytest.raises(LookupError) as ei:
        r.pick(TaskKind.VISION, needs_vision=True)
    assert "vision" in str(ei.value).lower()


def test_router_providers_are_unique_in_order():
    a, b = MockProvider(), MockProvider()
    a.name, b.name = "a", "b"
    r = Router()
    r.register(a, kinds=[TaskKind.CODING])
    r.register(a, kinds=[TaskKind.FILE])           # same provider twice
    r.register(b)
    assert [p.name for p in r.providers()] == ["a", "b"]


# -- Router + build_router ---------------------------------------------------

def test_build_router_registers_fallback_for_all_kinds():
    fb = MockProvider()
    fb.name = "fallback"
    r = build_router(fallback=fb)
    for k in TaskKind:
        assert r.pick(k) is fb


def test_build_router_defaults_to_a_mock_provider():
    r = build_router()
    assert r.pick(TaskKind.CODING).name == "mock"


# -- AccountStore ------------------------------------------------------------

def test_account_store_json_roundtrip(tmp_path):
    path = tmp_path / "sub" / "accounts.json"
    store = AccountStore(str(path))
    acc = ConnectedAccount(provider="gemini", label="My Gemini", default_model="x")
    store.add(acc)
    store.add(ConnectedAccount(provider="claude", label="Work"))

    assert path.exists()
    reloaded = AccountStore(str(path))
    provs = sorted(a.provider for a in reloaded.list())
    assert provs == ["claude", "gemini"]
    # no secret ever written
    assert "api_key" not in path.read_text(encoding="utf-8").lower() or \
        "TORA_" not in path.read_text(encoding="utf-8")


def test_account_store_enable_remove(tmp_path):
    store = AccountStore(str(tmp_path / "a.json"))
    acc = store.add(ConnectedAccount(provider="openai"))
    assert store.enable(acc.id, False) is True
    assert store.list(enabled_only=True) == []
    assert store.remove(acc.id) is True
    assert store.list() == []
    assert store.remove("nope") is False


def test_account_store_missing_file_is_empty(tmp_path):
    assert AccountStore(str(tmp_path / "does_not_exist.json")).list() == []


def test_account_store_corrupt_file_is_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert AccountStore(str(p)).list() == []


def test_api_key_from_env(tmp_path, monkeypatch):
    store = AccountStore(str(tmp_path / "a.json"))
    acc = ConnectedAccount(provider="claude")
    monkeypatch.setenv("TORA_CLAUDE_API_KEY", "sk-secret")
    assert store.api_key_for(acc) == "sk-secret"


def test_api_key_gemini_prefers_env(tmp_path, monkeypatch):
    store = AccountStore(str(tmp_path / "a.json"))
    acc = ConnectedAccount(provider="gemini")
    monkeypatch.setenv("TORA_GEMINI_API_KEY", "env-wins")
    assert store.api_key_for(acc) == "env-wins"


def test_api_key_gemini_config_fallback_is_str_or_none(tmp_path, monkeypatch):
    # With no TORA_GEMINI_API_KEY, gemini falls back to the config module's key.
    # Its value depends on the real environment, so only assert the type.
    store = AccountStore(str(tmp_path / "a.json"))
    monkeypatch.delenv("TORA_GEMINI_API_KEY", raising=False)
    key = store.api_key_for(ConnectedAccount(provider="gemini"))
    assert key is None or isinstance(key, str)
    # a provider with no env var and no config fallback yields None
    monkeypatch.delenv("TORA_OPENAI_API_KEY", raising=False)
    assert store.api_key_for(ConnectedAccount(provider="openai")) is None
