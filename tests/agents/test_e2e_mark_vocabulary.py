"""Mark vocabulary (PT-11, the Massive Direction Plan).

Ring, reticle, frame, arrow, pill and wash each mean one thing, and every
drawing Tora can make is one of them. A pointing mark says which it is: a
control found by the tree gets a ring, a bare exact spot a reticle, a
region or a guess a (dashed) frame.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import companion_manager as cm
from ui import marks, overlay


def test_every_drawing_is_one_of_the_six():
    src = inspect.getsource(overlay.CursorOverlay._draw_annotations)
    kinds = {"line", "arrow", "poly", "rect", "circle", "underline", "angle", "text"}
    for kind in kinds:
        assert f'"{kind}"' in src, f"the overlay no longer draws {kind}?"
        assert marks.mark_of({"kind": kind}) in marks.VOCABULARY
    assert len(marks.VOCABULARY) == 6


def _manager():
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    drawn = []
    mgr.sig_draw = SimpleNamespace(emit=lambda sh: drawn.append(marks.mark_of(sh)))
    return mgr, drawn


def test_pointing_marks_say_what_they_are():
    mgr, drawn = _manager()
    mgr._highlight(SimpleNamespace(x=10.0, y=10.0, bbox=(0, 0, 20, 20)))
    mgr._highlight(SimpleNamespace(x=10.0, y=10.0, bbox=None))
    mgr._frame_region(SimpleNamespace(x=10.0, y=10.0, bbox=(0, 0, 20, 20)))
    mgr._frame_region(SimpleNamespace(x=10.0, y=10.0, bbox=None))
    assert drawn == ["ring", "reticle", "frame", "frame"]
