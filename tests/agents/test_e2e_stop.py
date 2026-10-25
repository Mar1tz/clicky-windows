"""One cancel path (PT-01, the Massive Direction Plan of 25 Oct 2026).

Esc anywhere, the tray's Stop or "stop" ends everything she set going -
listening, her voice, the slow route's answer, typing into a box or a form -
and everything she has on screen. A tool still finishing when the stop came
takes back what it drew, and an older answer can no longer set the state.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import companion_manager as cm
from ai import form_fill, write_for_me
from ai.form_fill import Field
from companion_manager import AppState


def _manager():
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    seen = []
    for name in ("sig_point_release", "sig_clear_drawings", "sig_guide_release"):
        setattr(mgr, name, SimpleNamespace(emit=lambda *a, n=name: seen.append(n)))
    mgr.sig_trace = SimpleNamespace(emit=lambda state: seen.append(f"mist:{state}"))
    mgr.sig_state_changed = SimpleNamespace(emit=lambda s: seen.append(f"state:{s.name}"))
    mgr._begin_lock = threading.Lock()
    mgr._state, mgr._loop, mgr._answer_task, mgr._realtime, mgr._tts = AppState.SPEAKING, None, None, None, None
    mgr._stop_guide = lambda: seen.append("guide stopped")
    mgr._emit_state = lambda s: seen.append(f"state:{s.name}")
    return mgr, seen


def test_stop_clears_everything_and_retires_the_answer(monkeypatch):
    write_for_me.STOP.clear()
    mgr, seen = _manager()
    asked = mgr._question_no
    mgr.stop()
    assert {"sig_point_release", "sig_clear_drawings", "sig_guide_release", "mist:drop", "guide stopped",
            "state:IDLE"} <= set(seen)
    assert mgr._question_no == asked + 1, "an older answer can no longer set the state"
    assert write_for_me.STOP.is_set(), "typing stops at its next word"
    mgr.stop()                                              # a second stop is harmless
    write_for_me.STOP.clear()


def test_a_tool_that_ends_after_the_stop_takes_back_what_it_drew():
    mgr, seen = _manager()
    mgr._tools_in_flight, mgr._tool_done_at = 0, 0.0

    async def look(args):
        await asyncio.sleep(0.05)
        seen.append("tool drew an arrow")
        return {"ok": True}

    async def run():
        task = asyncio.ensure_future(mgr._clears_activity(look)({}))
        await asyncio.sleep(0.01)
        mgr.stop()
        seen.clear()
        await task
    asyncio.run(run())
    assert seen.index("tool drew an arrow") < seen.index("sig_clear_drawings"), "its arrow is taken back"
    write_for_me.STOP.clear()


def test_a_tool_with_no_stop_leaves_its_marks():
    mgr, seen = _manager()
    mgr._tools_in_flight, mgr._tool_done_at = 0, 0.0

    async def look(args):
        return {"ok": True}
    asyncio.run(mgr._clears_activity(look)({}))
    assert "sig_clear_drawings" not in seen


def test_typing_stops_at_the_next_word():
    write_for_me.STOP.clear()
    typed = []
    kb = SimpleNamespace(where=lambda: ("hwnd", "Gmail"), type=typed.append)
    t = write_for_me.Typist(kb, SimpleNamespace(set_text=lambda text: typed.append(("clipboard", text))))
    t.feed("Hello there, ")
    write_for_me.STOP.set()
    t.feed("and more words ")
    assert t.finish() == "stopped" and typed == ["Hello there, "], "nothing after Esc, nothing on the clipboard"
    write_for_me.STOP.clear()


def test_a_form_stops_at_the_next_box(monkeypatch):
    write_for_me.STOP.clear()
    kb = SimpleNamespace(where=lambda: ("hwnd", "Form"), select_all=lambda: None, type=lambda text: None)
    fields = [Field("f1", "Name", rect=(0, 0, 100, 20)), Field("f2", "Email", rect=(0, 40, 100, 60))]

    def on_field(f):
        write_for_me.STOP.set()                             # Esc while the first box is being filled
    monkeypatch.setattr(form_fill.uia_page, "focus", lambda c, s=1.0: None)
    monkeypatch.setattr(form_fill.uia_page, "has_focus", lambda c: True)
    monkeypatch.setattr(form_fill.uia_page, "value", lambda c: "Torben")
    result = form_fill.fill(fields, {"f1": "Torben", "f2": "t@example.com"}, keyboard=kb, on_field=on_field)
    assert result.stopped and [n for n, _v in result.done] == ["Name"]
    write_for_me.STOP.clear()
