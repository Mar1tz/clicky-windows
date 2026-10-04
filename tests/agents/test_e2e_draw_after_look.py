"""Tora's drawings after a screen look land on real things (2 Oct 2026).

In the first launch Tora boxed the wrong line of a sum correctly (that was
look_at_screen, which finds things in the image), then drew a big red
circle around empty space: a draw call whose coordinates the voice model
made up, because it never sees the screen and the look answered only in
words. The look now says where each highlight is, in the draw tool's own
0-1000 coordinates, so a drawing added after it can be placed on them.
Drawing itself is not restricted: teaching drawings still go through.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import companion_manager as cm


def _bare_manager():
    from companion_manager import CompanionManager

    mgr = CompanionManager.__new__(CompanionManager)
    drawn = []
    mgr.sig_draw = SimpleNamespace(emit=drawn.append)
    mgr.sig_clear_drawings = SimpleNamespace(emit=lambda: None)
    mgr._drawn = drawn
    return mgr


def test_a_look_says_where_its_highlights_are(monkeypatch):
    from ai import gemini_vision
    mgr = _bare_manager()
    mgr._privacy_guard = False
    mgr._activity = lambda what: None
    shot = SimpleNamespace(base64_jpeg="x")

    async def capture(**_kw):
        return [shot]
    mgr._capture_shots = capture
    mgr._active_shot = lambda screens: shot
    mgr._shape_from_tag = lambda tag: {"tag": tag}
    action = gemini_vision.Action(kind="rect", label="2x = 14 + 6", box=(300, 120, 360, 420))
    look = SimpleNamespace(answer="Line 2: +6 should be -6.", observations=[], actions=[action])

    async def fake_look(*_a, **_kw):
        return look
    monkeypatch.setattr(gemini_vision, "look", fake_look)
    monkeypatch.setattr(cm, "active_window_title", lambda: "HeyTora")
    out = asyncio.run(mgr._tool_look_at_screen({"question": "what's wrong with this equation?"}))
    assert out["highlights"] == [{"label": "2x = 14 + 6", "tag": "[RECT:120,300,420,360:tora]"}]
    assert mgr._drawn == [{"tag": "[RECT:120,300,420,360:tora]"}], "and it is drawn, as before"


def test_drawing_is_not_restricted_after_a_look():
    """Teaching draws on what it explains, after looking: that must still work."""
    mgr = _bare_manager()
    mgr._extract_shapes = lambda tags: [{"kind": "arrow", "tags": tags}]
    out = asyncio.run(mgr._tool_draw({"tags": "[ARROW:100,100->400,300:red]"}))
    assert out == {"ok": True, "drawn": 1} and len(mgr._drawn) == 1


def test_the_draw_tool_tells_her_to_use_the_looks_coordinates():
    from ai import gemini_realtime
    tools = getattr(gemini_realtime, "TOOLS", None) or getattr(gemini_realtime, "_TOOLS", None)
    text = repr(tools) if tools is not None else open(gemini_realtime.__file__, encoding="utf-8").read()
    assert "highlights" in text and "Never invent" in text
