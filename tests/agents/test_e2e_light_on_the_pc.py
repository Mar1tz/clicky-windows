"""HeyTora must be light on the PC (5 Oct 2026).

"On some PCs it keeps causing the PC to crash ... the laptop's fans go on and
it's very hot." The overlay repainted every monitor with anything on it,
whole, 60 times a second - and the buddy is always on one - so Windows
composed a full-monitor see-through picture from Tora 60 times a second, for
as long as HeyTora ran: a third of a core on a 1536x864 screen, many times
that on 4K or three monitors, on top of the desktop compositor. And any error
raised inside something Qt called ended the app in Qt6Core.dll (0xc0000409),
logging nothing.

Now the overlay paints the buddy's few pixels and what it just left, a still
buddy every third frame, the idle flow bar not at all; errors are logged and
HeyTora carries on; a crash below Python leaves its stacks in crash.log.
"""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(qapp, monkeypatch):
    from PyQt6.QtCore import QPoint
    import ui.overlay as overlay_mod

    class StillCursor:
        @staticmethod
        def pos():
            return QPoint(300, 300)

    monkeypatch.setattr(overlay_mod, "QCursor", StillCursor)
    ov = overlay_mod.CursorOverlay()
    ov._tick_timer.stop()                 # the test drives the frames
    ov.show()
    for s in ov._surfaces:
        s.rects = []
    yield ov
    ov.hide()
    for s in ov._surfaces:
        s.deleteLater()


def _painted(ov):
    """Pixels repainted at the last frame, over every monitor."""
    return sum(r.width() * r.height() for s in ov._surfaces for r in s.rects)


def _monitors(ov):
    return sum(s.width() * s.height() for s in ov._surfaces)


def test_the_resting_buddy_repaints_its_own_few_pixels_not_the_monitor(overlay):
    for _ in range(90):                    # let the spring settle on the cursor
        overlay._tick()
    overlay._refresh()
    assert 0 < _painted(overlay) <= 100 * 100, "the buddy and its glow - not the screen"
    assert _painted(overlay) < _monitors(overlay) / 20


def test_a_still_buddy_is_painted_every_third_frame(overlay, monkeypatch):
    for _ in range(90):
        overlay._tick()
    calls = []
    monkeypatch.setattr(overlay, "_refresh", lambda force=False: calls.append(force))
    for _ in range(30):
        overlay._tick()
    assert 9 <= len(calls) <= 11, "the slow breath shows just as well at 20 fps"
    overlay.set_mode("listening")          # the voice bars move with the voice: every frame
    calls.clear()
    for _ in range(30):
        overlay._tick()
    assert len(calls) == 30


def test_what_was_drawn_is_rubbed_out_where_it_was(overlay):
    from PyQt6.QtCore import QPointF
    overlay._refresh()
    before = list(overlay._surfaces[0].rects)
    updated = []
    s = overlay._surfaces[0]
    s.update = lambda r=None: updated.append(r)
    overlay._display_pos = QPointF(600, 450)   # it moved (the test screen is 800 x 600)
    overlay._vel = QPointF(5, 5)
    overlay._refresh()
    assert all(r in updated for r in before), "the old place is repainted, so nothing is left behind"
    assert any(r not in before for r in updated)


def test_a_drawing_repaints_the_monitors_while_it_draws_and_then_stops(overlay, monkeypatch):
    import ui.overlay as overlay_mod
    overlay.add_shape({"kind": "line", "pts": [(100, 100), (400, 100)], "ttl": None})
    overlay._refresh()
    assert _painted(overlay) == _monitors(overlay), "drawing in: wherever it is"
    later = time.monotonic() + 5.0             # long after it has been drawn
    monkeypatch.setattr(overlay_mod.time, "monotonic", lambda: later)
    overlay._refresh()
    assert _painted(overlay) < _monitors(overlay) / 20, "drawn and still: only the buddy again"
    overlay.clear_annotations()
    overlay._refresh()
    assert _painted(overlay) == _monitors(overlay), "cleared: rubbed out wherever it was"


def test_the_idle_flow_bar_is_not_repainted(qapp):
    from ui.flow_bar import FlowBar
    bar = FlowBar()
    try:
        updates = []
        bar.pill.update = lambda *a: updates.append(1)
        bar._retarget()
        bar._size = bar._size_from = bar._size_to
        for _ in range(60):
            bar._tick()
        assert len(updates) <= 1, "idle and settled: nothing changes, nothing is painted"
        bar.set_state("listening")
        updates.clear()
        for _ in range(10):
            bar._tick()
        assert len(updates) == 10, "listening: the bars move every frame"
    finally:
        bar.close()


# -- when something goes wrong ----------------------------------------------------------------------------------------

@pytest.fixture
def guards(tmp_path, monkeypatch):
    """main._install_crash_guards against a tmp data folder, undone after."""
    import config
    import main
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    hook, thook = sys.excepthook, threading.excepthook
    was_on = faulthandler.is_enabled()
    yield main, tmp_path
    sys.excepthook, threading.excepthook = hook, thook
    faulthandler.disable()
    if was_on:
        faulthandler.enable()
    if main._crash_file is not None:
        main._crash_file.close()
        main._crash_file = None


def test_an_error_in_anything_qt_calls_is_logged_and_heytora_carries_on(guards, caplog):
    main, _ = guards
    main._install_crash_guards()
    assert sys.excepthook is not sys.__excepthook__, "PyQt calls qFatal() only for Python's own hook"
    with caplog.at_level(logging.ERROR, logger="tora.crash"):
        try:
            raise RuntimeError("a slot went wrong")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    assert "a slot went wrong" in caplog.text


def test_an_error_in_another_thread_is_logged(guards, caplog):
    main, _ = guards
    main._install_crash_guards()

    def work():
        raise ValueError("in a worker")

    with caplog.at_level(logging.ERROR, logger="tora.crash"):
        t = threading.Thread(target=work, name="worker")
        t.start()
        t.join()
    assert "in a worker" in caplog.text and "worker" in caplog.text


def test_a_crash_leaves_its_stacks_and_the_next_start_says_so(guards, caplog):
    main, folder = guards
    (folder / "crash.log").write_text(
        "\n=== HeyTora started 2026-10-05 10:00:00 pid 1 ===\n"
        "Windows fatal exception: access violation\n\nThread 0x01 (most recent call first):\n"
        '  File "ui\\overlay.py", line 1 in _paint\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="tora.crash"):
        main._install_crash_guards()
    assert "the last run crashed" in caplog.text and "access violation" in caplog.text
    assert faulthandler.is_enabled()
    text = (folder / "crash.log").read_text(encoding="utf-8")
    assert text.rstrip().splitlines()[-1].startswith("=== HeyTora started"), "this run's marker"


def test_handled_com_errors_are_not_called_a_crash(guards, caplog):
    """faulthandler writes down every Windows exception as it is raised, the
    handled ones too: 1.7.0 logged fifteen 0x80010108 (a COM object gone - an
    app Tora was reading closed) in seven hours of normal running, and the
    next start announced a crash."""
    main, folder = guards
    (folder / "crash.log").write_text(
        "\n=== HeyTora started 2026-10-05 16:03:41 pid 1 ===\n"
        + "Windows fatal exception: code 0x80010108\n\nThread 0x01 (most recent call first):\n  <no Python frame>\n" * 3,
        encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="tora.crash"):
        main._install_crash_guards()
    assert "crashed" not in caplog.text
    assert "3 handled Windows error(s) (code 0x80010108) - not crashes" in caplog.text
    assert faulthandler.is_enabled(), "and the recorder is still on"


def test_a_clean_last_run_says_nothing(guards, caplog):
    main, folder = guards
    (folder / "crash.log").write_text("\n=== HeyTora started 2026-10-05 10:00:00 pid 1 ===\n",
                                      encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="tora.crash"):
        main._install_crash_guards()
    assert "crashed" not in caplog.text


def test_after_an_arrow_the_buddy_rests_off_its_head(overlay, monkeypatch):
    """SP-11: it rode the pen tip and then sat on the arrowhead for 2.5 s."""
    import math
    import ui.overlay as overlay_mod
    t = [time.monotonic()]
    monkeypatch.setattr(overlay_mod.time, "monotonic", lambda: t[0])
    overlay.add_shape({"kind": "arrow", "pts": [(100.0, 100.0), (400.0, 500.0)], "ttl": None})
    shape = overlay._annotations[-1]
    t[0] = shape["start"] + shape["dur"] * 0.5
    overlay._tick()                                        # drawing: on the pen tip
    t[0] = shape["start"] + shape["dur"] + 0.5             # drawn; the lesson is still on screen
    for _ in range(40):
        overlay._tick()
    head = (400.0 + 6, 500.0 + 6)                          # where it used to rest
    pos = overlay._display_pos
    assert math.hypot(pos.x() - head[0], pos.y() - head[1]) > 35, "the head is in view"
    assert abs(pos.x() - (376 + 6)) < 2 and abs(pos.y() - (468 + 6)) < 2, "back along the arrow"
