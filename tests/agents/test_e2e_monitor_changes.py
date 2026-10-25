"""Monitor and DPI changes (PT-10, the Massive Direction Plan).

Unplugging, moving or rescaling a monitor moves everything on it. The
layout has a generation; an arrow, a control list or a screenshot taken
under an older one is no longer trusted, and the overlay clears its marks
before the next one is drawn.
"""
from __future__ import annotations

import time

import companion_manager as cm
from screen import monitors

TWO = [(0, 0, 1920, 1080), (1920, 0, 1920, 1080)]


def test_only_a_real_change_is_a_new_layout():
    monitors.publish(TWO)
    gen = monitors.generation()
    assert monitors.publish(TWO) is False and monitors.generation() == gen
    assert monitors.publish(TWO[:1]) is True and monitors.generation() == gen + 1   # unplugged
    assert monitors.publish([(0, 0, 1536, 864)]) is True                            # rescaled to 125%
    monitors.publish([])


def test_positions_from_the_old_layout_are_dropped(monkeypatch):
    monkeypatch.setattr(cm, "active_window_title", lambda: "Notepad")
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    monitors.publish(TWO)
    mgr._remember_ref(100, 100, "Save")
    mgr._pointables, mgr._pointables_at, mgr._pointables_title = [(1, 1, "Save", "ButtonControl")], time.monotonic(), "Notepad"
    mgr._pointables_gen = monitors.generation()
    assert mgr._ref_fresh() and mgr._snapshot_fresh()
    monitors.publish(TWO[:1])                     # the second monitor unplugged
    assert not mgr._ref_fresh(), "the last arrow's spot means nothing now"
    assert not mgr._snapshot_fresh(), "nor the control list's positions"
    monitors.publish([])


def test_the_overlay_clears_its_marks_when_the_layout_moves():
    from PyQt6.QtWidgets import QApplication
    from ui.overlay import CursorOverlay
    app = QApplication.instance() or QApplication([])
    ov = CursorOverlay()
    try:
        ov._sync_surfaces()                       # the layout as it is
        ov._annotations = [{"kind": "rect", "start": 0.0}]
        ov._sync_surfaces()                       # nothing moved: the marks stay
        assert ov._annotations
        monitors.publish([(5000, 0, 800, 600)])   # as if a monitor had been unplugged
        ov._sync_surfaces()                       # and Qt's layout comes back different
        assert ov._annotations == [], "drawn for the old layout, gone before the next mark"
    finally:
        ov.close()
        monitors.publish([])
