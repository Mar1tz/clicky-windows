"""Marks on the phrase that names them (PT-14, the Massive Direction Plan).

"First File, then Save": the model decides both points before it speaks,
and both arrows used to go up at once. Each now waits for her voice to
reach a word that names it - by the clock the answer card keeps time with -
or at most 2.5 s; a stop or a new question drops what is still waiting.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import companion_manager as cm
from ai import phrase_gate as pg
from ai.phrase_gate import PhraseGate
from ai.point_evidence import Evidence
from companion_manager import AppState


def test_a_mark_goes_up_when_its_word_is_heard():
    gate, up = PhraseGate(), []
    gate.wait("File", lambda: up.append("File"), now=0.0)
    gate.wait("Save", lambda: up.append("Save"), now=0.0)
    gate.said("First open the File menu, then click Save at the bottom.")   # received at once
    gate.poll((0.3, 4.0), now=0.4)                 # 0.3 s of 4 s heard: "First open the"
    assert up == []
    gate.poll((1.6, 4.0), now=1.7)                 # "...the File menu, then"
    assert up == ["File"]
    gate.poll((3.6, 4.0), now=3.7)
    assert up == ["File", "Save"]


def test_a_label_she_never_says_goes_up_late_not_never():
    gate, up = PhraseGate(), []
    gate.wait("Search or enter address", lambda: up.append(1), now=0.0)
    gate.said("Type it up there.")
    gate.poll((2.0, 2.0), now=1.0)
    assert up == []
    gate.poll((2.0, 2.0), now=pg.MAX_WAIT_S + 0.1)
    assert up == [1]


def test_a_long_name_is_named_by_its_longer_words():
    assert pg.names("Search or enter address", "click the address bar")
    assert not pg.names("Search or enter address", "click the box")
    assert pg.names("OK", "press ok")


def _manager():
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    drawn = []
    mgr.sig_point_at = SimpleNamespace(emit=lambda *a: drawn.append("arrow"))
    mgr.sig_point_hold = SimpleNamespace(emit=lambda *a: None)
    mgr.sig_draw = SimpleNamespace(emit=lambda sh: drawn.append(sh["kind"]))
    for name in ("sig_point_release", "sig_clear_drawings", "sig_guide_release"):
        setattr(mgr, name, SimpleNamespace(emit=lambda *a: None))
    mgr.sig_trace = SimpleNamespace(emit=lambda *a: None)
    mgr._remember_ref = lambda *a: None
    mgr._loop, mgr._realtime, mgr._state = None, object(), AppState.THINKING
    mgr.voice_clock = lambda: None
    mgr._phrase_gate = None
    mgr._begin_lock = threading.Lock()
    return mgr, drawn


def _found():
    return SimpleNamespace(x=10.0, y=10.0, label="Save", bbox=(0, 0, 20, 20), evidence=Evidence("uia"))


def test_while_she_answers_the_arrow_waits_for_its_word():
    mgr, drawn = _manager()
    assert mgr._show_point(_found(), "save", hold=False)
    assert drawn == [], "decided, not yet said"
    mgr._on_realtime_said = cm.CompanionManager._on_realtime_said.__get__(mgr)
    mgr.sig_response_chunk = SimpleNamespace(emit=lambda *a: None)
    mgr._on_realtime_said("Click Save.")
    mgr.voice_clock = lambda: (1.0, 1.0)
    mgr._poll_gate()
    assert drawn == ["arrow", "rect"]


def test_a_stop_drops_a_mark_still_waiting():
    mgr, drawn = _manager()
    mgr._show_point(_found(), "save", hold=False)
    mgr._clear_marks()
    mgr._gate().said("Click Save.")
    mgr.voice_clock = lambda: (1.0, 1.0)
    mgr._poll_gate()
    assert drawn == []


def test_a_mark_from_an_older_question_never_goes_up():
    mgr, drawn = _manager()
    mgr._show_point(_found(), "save", hold=False)
    mgr._question_no += 1                          # the next question began
    mgr._gate().said("Click Save.")
    mgr.voice_clock = lambda: (1.0, 1.0)
    mgr._poll_gate()
    assert drawn == []


def test_when_she_is_not_talking_it_is_drawn_at_once():
    mgr, drawn = _manager()
    mgr._realtime = None
    mgr._show_point(_found(), "save", hold=False)
    assert drawn == ["arrow", "rect"]
