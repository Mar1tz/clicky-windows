"""Confidence from evidence (PT-18, the Massive Direction Plan).

An arrow claims "exactly this". It is drawn for one enabled, on-screen,
uncovered match in the app's own list of controls; a picture's guess, one
of several matches, a greyed-out or covered control get a frame round the
region instead; an old picture or no match gets a question.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import companion_manager as cm
from ai import point_evidence as pe
from ai.point_evidence import ASK, EXACT, REGION, Evidence


def test_the_rules():
    assert pe.judge(Evidence("uia", unique=True, enabled=True, visible=True, occluded=False)) == EXACT
    assert pe.judge(Evidence("uia")) == EXACT, "what cannot be told is not held against it"
    assert pe.judge(Evidence("snapshot")) == EXACT
    assert pe.judge(Evidence("uia", unique=False)) == REGION
    assert pe.judge(Evidence("uia", enabled=False)) == REGION
    assert pe.judge(Evidence("uia", occluded=True)) == REGION
    assert pe.judge(Evidence("uia", visible=False)) == REGION
    assert pe.judge(Evidence("vision", frame_age=0.4)) == REGION
    assert pe.judge(Evidence("ocr", frame_age=0.1)) == REGION
    assert pe.judge(Evidence("vision", frame_age=6.0)) == ASK
    assert pe.judge(Evidence("")) == ASK
    assert pe.judge(None) == EXACT, "a tier that says nothing keeps the old arrow"
    assert "more than one thing matched" in Evidence("uia", unique=False).doubts()


def _manager():
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    drawn = []
    mgr.sig_point_at = SimpleNamespace(emit=lambda *a: drawn.append(("arrow", a)))
    mgr.sig_point_hold = SimpleNamespace(emit=lambda *a: None)
    mgr.sig_draw = SimpleNamespace(emit=lambda sh: drawn.append((sh["kind"], sh)))
    mgr._remember_ref = lambda *a: None
    mgr._pointing_held = False
    return mgr, drawn


def _found(evidence, bbox=(100, 100, 180, 130)):
    return SimpleNamespace(x=140.0, y=115.0, label="Save", bbox=bbox, evidence=evidence)


def test_an_exact_match_gets_the_arrow():
    mgr, drawn = _manager()
    assert mgr._show_point(_found(Evidence("uia")), "save")
    assert drawn[0][0] == "arrow"


def test_a_guess_is_framed_not_pointed_at():
    mgr, drawn = _manager()
    assert mgr._show_point(_found(Evidence("vision", frame_age=0.3)), "save")
    assert [k for k, _ in drawn] == ["rect"], "a frame, and no arrow"
    rect = drawn[0][1]
    assert rect["x1"] < 100 and rect["x2"] > 180


def test_an_old_picture_asks_instead(monkeypatch):
    mgr, drawn = _manager()
    assert not mgr._show_point(_found(Evidence("vision", frame_age=9.0)), "save")
    assert not drawn and "old" in mgr._point_doubt

    async def point(name, hold=True):
        return mgr._show_point(_found(Evidence("vision", frame_age=9.0)), name)
    mgr._point_to = point
    result = asyncio.run(mgr._tool_point_at({"name": "save"}))
    assert result["found"] is False and "Ask the user" in result["hint"] and result["unsure"]


def test_the_tree_tier_records_what_it_knows(monkeypatch):
    from ai import hybrid_pointer
    monkeypatch.setattr(pe, "covered", lambda x, y, hwnd: True)
    root = SimpleNamespace(BoundingRectangle=SimpleNamespace(left=0, top=0, right=800, bottom=600),
                           NativeWindowHandle=42)
    ev = hybrid_pointer._uia_evidence(root, "Save", 140, 115, unique=False)
    assert ev.source == "uia" and ev.unique is False and ev.visible is True and ev.occluded is True
    assert pe.judge(ev) == REGION
    off = hybrid_pointer._uia_evidence(root, "Save", 900, 115, unique=True)
    assert off.visible is False


def test_what_is_on_top_skips_heytoras_own_windows():
    assert isinstance(pe.top_window_at(5, 5), int)
    assert pe.covered(5, 5, 0) is None
