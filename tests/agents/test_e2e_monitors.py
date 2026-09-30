"""Several monitors at different scalings (30 Sep 2026).

Measured on the user's PC: a 100% Dell in the middle as the main monitor,
a 125% screen either side. Qt keeps each monitor's top-left corner where
Windows has it and scales only its size; Tora divided every pixel by one
scaling for the whole desktop and drew the overlay as ONE window across all
three. Arrows landed hundreds of pixels off on the side monitors, and the
cursor went "a mess" when it crossed onto them. These tests hold the fixes:
one conversion per monitor, one overlay window per monitor, and the caption
always on the main one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _layout():
    from screen.monitors import Monitor
    return [
        Monitor(r"\\.\DISPLAY1", 1920, 0, 1920, 1080, 1.25),                 # right, 125%
        Monitor(r"\\.\DISPLAY4", 0, 0, 1920, 1080, 1.0, primary=True),       # the Dell, main
        Monitor(r"\\.\DISPLAY5", -1920, 0, 1920, 1080, 1.25),                # left, 125%
    ]


@pytest.fixture
def three(monkeypatch):
    from screen import monitors
    layout = _layout()
    monkeypatch.setattr(monitors, "monitors", lambda: layout)
    return layout


# -- one conversion per monitor ----------------------------------------------------------------------------

def test_each_monitor_keeps_its_corner_and_its_own_scaling(three):
    from screen import monitors
    right, main, left = three
    assert right.logical == (1920.0, 0.0, 1536.0, 864.0), "as Qt reported it"
    assert left.logical == (-1920.0, 0.0, 1536.0, 864.0)
    # The pointer at physical (-757, 950) was at logical (-990, 760) by Qt.
    x, y = monitors.to_logical(-757, 950)
    assert (round(x, 1), round(y, 1)) == (-989.6, 760.0)
    assert monitors.to_logical(500, 300) == (500.0, 300.0), "the 100% main monitor is unchanged"
    assert monitors.to_logical(2500, 500) == (2384.0, 400.0)


def test_the_old_rule_was_wrong_on_both_side_monitors(three):
    """physical / one scaling: what Tora did, by how far it missed."""
    from screen import monitors
    for px, py in ((-757, 950), (2500, 500)):
        right = monitors.to_logical(px, py)
        wrong = (px / 1.0, py / 1.0)             # the Dell's scaling was the system's
        assert abs(right[0] - wrong[0]) > 100


def test_there_and_back_again(three):
    from screen import monitors
    for px, py in ((-1900, 10), (-1, 1079), (0, 0), (1919, 500), (1920, 0), (3839, 1079)):
        lx, ly = monitors.to_logical(px, py)
        bx, by = monitors.to_physical(lx, ly)
        assert (round(bx), round(by)) == (px, py)


def test_a_box_stays_on_one_monitor_and_keeps_its_shape(three):
    from screen import monitors
    assert monitors.rect_to_logical(1900, 100, 2000, 140) == pytest.approx(
        (1904.0, 80.0, 1984.0, 112.0)), "straddling the edge: converted on the right-hand monitor"


def test_the_main_monitor_and_the_nearest_one(three):
    from screen import monitors
    assert monitors.primary().name == r"\\.\DISPLAY4"
    assert monitors.monitor_at(5000, 500).name == r"\\.\DISPLAY1"
    assert monitors.monitor_at_logical(-200, 100).name == r"\\.\DISPLAY5", "the gap: nearest"


def test_qts_own_rectangles_win_matched_by_corner_not_name():
    """Qt calls the Dell "DELL SE2426HG", not \\\\.\\DISPLAY4 - so by corner."""
    from screen import monitors
    got = {m.name: m.logical for m in monitors._with_qt(
        _layout(), [(0.0, 0.0, 1920.0, 1080.0), (1920.0, 0.0, 1500.0, 850.0)])}
    assert got[r"\\.\DISPLAY1"] == (1920.0, 0.0, 1500.0, 850.0), "as Qt has it"
    assert got[r"\\.\DISPLAY5"] == (-1920.0, 0.0, 1536.0, 864.0), "Qt said nothing: the rule"


def test_a_screenshot_knows_its_monitors_own_scaling_and_corner(three):
    from screen.capture import _placement
    assert _placement(-1920, 0, three) == (1.25, -1920, 0)
    assert _placement(0, 0, three) == (1.0, 0, 0)
    assert _placement(1920, 0, three) == (1.25, 1920, 0)


# -- the arrows ---------------------------------------------------------------------------------------------

def test_a_local_hit_on_a_side_monitor_lands_where_the_overlay_draws(three):
    from companion_manager import CompanionManager
    target = SimpleNamespace(x=-757, y=950, bbox=(-800, 930, -714, 970), label="Save", source="uia")
    task = SimpleNamespace(result=lambda: target)
    hit = CompanionManager._local_hit(SimpleNamespace(), task, None, "Save")
    assert (round(hit.x, 1), round(hit.y, 1)) == (-989.6, 760.0)
    assert hit.bbox == pytest.approx((-1024.0, 744.0, -955.2, 776.0))


def test_vision_on_a_side_monitor(monkeypatch):
    import asyncio
    from ai import gemini_vision

    async def generate_json(*_a, **_k):
        return {"found": True, "label": "Save", "box": [490, 490, 510, 510]}   # the middle

    monkeypatch.setattr(gemini_vision.gemini, "generate_json", generate_json)
    found = asyncio.run(gemini_vision.locate(
        "Save", screenshot_jpeg_b64="AAAA", original_width=1280, original_height=720,
        physical_width=1920, physical_height=1080, physical_left=1920, physical_top=0,
        dpi_scale=1.25))
    assert (found.x, found.y) == (1920 + 768, 432), "the middle of the right-hand monitor, as Qt has it"


def test_a_click_on_a_side_monitor_is_checked_in_the_same_pixels(three):
    from tutor_features.step_watch import StepWatcher
    w = StepWatcher("Click Save", judge=None, dpi_scale=1.0)
    lx, ly = w._to_logical(-757, 950)
    assert (round(lx, 1), round(ly, 1)) == (-989.6, 760.0)


# -- one overlay window per monitor, the caption on the main one ---------------------------------------------

_SCREENS = {"screens": [
    {"name": "DELL", "x": 0, "y": 0, "width": 1920, "height": 1080,
     "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1.0},
    {"name": "ARZOPA", "x": -1920, "y": 0, "width": 1536, "height": 864,
     "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1.25},
    {"name": "DISPLAY1", "x": 1920, "y": 0, "width": 1536, "height": 864,
     "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1.25},
]}

_PROBE = textwrap.dedent("""
    import json, sys, time
    sys.path.insert(0, ROOT)
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QCursor
    from PyQt6.QtWidgets import QApplication
    app = QApplication([])
    from ui.overlay import CursorOverlay
    o = CursorOverlay()
    o.show()
    out = {"screens": [[s.name(), list(s.geometry().getRect())] for s in app.screens()],
           "surfaces": [[s.qscreen.name(), list(s.geometry().getRect()), s.isVisible()] for s in o._surfaces],
           "hidden_controller": not o.isVisible(),
           "main": [app.primaryScreen().name(), list(app.primaryScreen().availableGeometry().getRect())]}
    # the caption: always the main monitor, whichever the pointer is on
    QCursor.setPos(QPoint(2500, 400))
    o.caption_state("SPEAKING"); o.caption_chunk("Sure, it is in the File menu. "); o.caption_done()
    for _ in range(40):
        o._tick(); app.processEvents(); time.sleep(0.005)
    r = o._caption._pill
    out["pill"] = [r.x(), r.y(), r.width(), r.height()]
    # the buddy crossing onto another monitor jumps rather than glides
    QCursor.setPos(QPoint(500, 400)); [o._tick() for _ in range(90)]
    QCursor.setPos(QPoint(-1000, 400)); o._tick()
    d = o._display_pos
    out["after_cross"] = [d.x(), d.y()]
    # only the monitors with something on them are repainted
    calls = {s.qscreen.name(): 0 for s in o._surfaces}
    for s in o._surfaces:
        s.update = (lambda n: (lambda *a: calls.__setitem__(n, calls[n] + 1)))(s.qscreen.name())
    o._caption.clear(); o._tick(); o._tick(); o._tick()
    for n in calls: calls[n] = 0
    for _ in range(10): o._tick()
    out["repaints"] = calls
    print(json.dumps(out))
""")


def test_one_window_per_monitor_and_the_caption_on_the_main_one(tmp_path):
    pytest.importorskip("PyQt6")
    (tmp_path / "screens.json").write_text(json.dumps(_SCREENS), encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen:configfile=screens.json")
    proc = subprocess.run([sys.executable, "-c", f"ROOT = {str(ROOT)!r}\n" + _PROBE],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    screens = {n: g for n, g in out["screens"]}
    assert len(out["surfaces"]) == 3 and out["hidden_controller"]
    for name, geo, visible in out["surfaces"]:
        want = screens[name]
        assert visible and geo[:3] == want[:3] and geo[3] == want[3] - 2, \
            f"{name}: its own window, exactly over it (less the taskbar's 2 px)"
    # Qt's main monitor (on Windows, the one at 0,0 - the Dell here; the
    # test platform picks the leftmost), not the one the pointer was on.
    main_name, (mx, my, mw, mh) = out["main"]
    assert main_name != "DISPLAY1"
    x, y, w, h = out["pill"]
    assert mx <= x and x + w <= mx + mw and y == my, "on the main monitor though the pointer was not"
    assert abs((x + w / 2) - (mx + mw / 2)) <= 1, "in its middle"
    assert out["after_cross"] == [-1000 + 35, 400 + 25], "jumped onto the new monitor"
    assert out["repaints"]["ARZOPA"] > 0, "the monitor the buddy is on"
    assert out["repaints"]["DISPLAY1"] == 0, "not the empty one"
