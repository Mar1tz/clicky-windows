"""The overlay redesign (the "HeyTora overlay redesign" doc, OD-01..09).

One test section per milestone. The classic look stays exactly as it is
behind the new_overlay switch until the new one is signed off, so these
tests check the new look and that nothing leaks between the two.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

ROOT = Path(__file__).resolve().parents[2]
# Files that draw the new look. They take every colour from ui/overlay_theme.py.
NEW_LOOK_FILES = ["ui/overlay_paint.py"]


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def overlay(qapp):
    from ui.overlay import CursorOverlay
    o = CursorOverlay()
    yield o
    o._tick_timer.stop()
    o.deleteLater()


# -- OD-01: tokens, the switch, the preview ---------------------------------------------------------------

def test_the_overlay_takes_the_apps_colours(qapp):
    from ui import app_theme, overlay_theme as t
    assert (t.ACCENT, t.SURFACE, t.BORDER, t.TEXT, t.TEXT_DIM) == (
        app_theme.ACCENT, app_theme.SURFACE, app_theme.BORDER_STRONG, app_theme.TEXT, app_theme.TEXT_DIM)
    assert t.color(t.ACCENT, 0.5).alphaF() == pytest.approx(0.5, abs=0.01)


def test_the_classic_blue_is_defined_once(qapp):
    from ui import caption, overlay, overlay_theme as t
    for f in ("ui/overlay.py", "ui/caption.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert not re.search(r"0x33,\s*0x80,\s*0xFF|3380FF", src, re.I), f"{f} defines its own blue"
    assert overlay.CURSOR_BLUE.name() == caption.CAP_BLUE.name() == t.CLASSIC_BLUE.lower()


def test_the_new_look_holds_no_colours_of_its_own():
    for f in NEW_LOOK_FILES:
        path = ROOT / f
        if not path.exists():
            continue                                   # written in a later milestone
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"QColor\(\s*\d", src), f"{f}: a colour literal - use ui/overlay_theme"
        assert not re.search(r"#[0-9A-Fa-f]{6}\b", src), f"{f}: a hex colour - use ui/overlay_theme"


def test_the_switch_is_saved_and_classic_until_signed_off():
    import config
    assert config.Config.SWITCHES["new_overlay"] == ("TORA_NEW_OVERLAY", False)
    assert config.cfg.switch("new_overlay") is False
    assert 'set_style("new" if cfg.switch("new_overlay") else "classic")' in (ROOT / "main.py").read_text(
        encoding="utf-8")


def test_the_cursor_and_the_island_switch_together(overlay):
    assert overlay.style == "classic" and overlay._caption.style == "classic"
    overlay.set_style("new")
    assert overlay.style == overlay._caption.style == "new"
    with pytest.raises(ValueError):
        overlay.set_style("sparkly")
    overlay.set_style("classic")
    assert overlay._caption.style == "classic"


def test_the_preview_covers_every_state():
    from PyQt6.QtCore import QRectF

    from ui.overlay_preview import REQUIRED, Stage, scenes
    names = [s.name for s in scenes(Stage(QRectF(0, 0, 1920, 1040)))]
    assert set(names) >= REQUIRED and len(names) == len(set(names))


def test_the_preview_renders_every_state_in_both_looks_without_a_window(qapp, tmp_path):
    from ui.overlay_preview import BACKDROPS, REQUIRED, shots
    paths = shots(str(tmp_path), ("classic", "new"), settle_scale=0.05)
    assert len(paths) == 2 * len(REQUIRED) * len(BACKDROPS)
    assert all(Path(p).stat().st_size > 2000 for p in paths)
    assert {p.split("-", 2)[2].rsplit("-", 1)[0] for p in map(lambda x: Path(x).name, paths)} == REQUIRED


# -- OD-02: quiet when idle ---------------------------------------------------------------------------------

def _spy_updates(overlay):
    """Count redraw requests; the overlay asks for one with self.update()."""
    calls = []
    overlay.update = lambda *a: calls.append(1)
    return calls


def _at_rest(overlay, x=500, y=400):
    from PyQt6.QtCore import QPoint, QPointF
    overlay._pointer_pos = lambda: QPoint(x, y)
    dx, dy = overlay._follow_offset()
    overlay._display_pos = QPointF(x + dx, y + dy)
    overlay._vel = QPointF(0, 0)


@pytest.mark.parametrize("style", ["classic", "new"])
def test_nothing_redraws_at_rest_and_the_timer_slows(overlay, style):
    from PyQt6.QtCore import QPoint
    from ui.overlay import FRAME_MS, IDLE_POLL_MS
    overlay.set_style(style)
    _at_rest(overlay)
    overlay._tick()                                    # the style change itself is drawn once
    calls = _spy_updates(overlay)
    for _ in range(120):                               # two seconds of ticks
        overlay._tick()
    assert calls == [] and overlay._tick_timer.interval() == IDLE_POLL_MS
    overlay._pointer_pos = lambda: QPoint(700, 420)    # you move the mouse
    overlay._tick()
    assert calls and overlay._tick_timer.interval() == FRAME_MS


def test_listening_and_thinking_keep_drawing(overlay):
    _at_rest(overlay)
    overlay._tick()
    calls = _spy_updates(overlay)
    for mode in ("listening", "thinking"):
        overlay.set_mode(mode)
        calls.clear()
        overlay._tick()
        overlay._tick()
        assert len(calls) == 2, mode


def test_a_new_look_arrow_stops_drawing_once_it_has_arrived(overlay):
    overlay.set_style("new")
    _at_rest(overlay)
    overlay.guide_point(600.0, 300.0, "Export")
    overlay._fly_t0 -= 5.0                             # the flight is over
    overlay._tick()
    assert overlay._flight_phase == "dwelling"
    overlay._arrived_at -= 5.0                         # and so is its arrival
    calls = _spy_updates(overlay)
    for _ in range(30):
        overlay._tick()
    assert calls == [] and overlay._flight_scale == 1.0, "no breathing on the target"


def test_the_classic_look_still_breathes_on_the_target(overlay):
    overlay.set_style("classic")
    _at_rest(overlay)
    overlay.guide_point(600.0, 300.0, "Export")
    overlay._fly_t0 -= 5.0
    overlay._tick()
    calls = _spy_updates(overlay)
    for _ in range(10):
        overlay._tick()
    assert len(calls) == 10, "classic stays exactly as it was until OD-09"


def _painted_outside(overlay, radius):
    """Pixels the overlay paints more than `radius` px from the cursor's anchor."""
    from PyQt6.QtCore import QRect
    c = overlay._display_pos
    img = overlay.grab(QRect(int(c.x() - overlay.x()) - 40, int(c.y() - overlay.y()) - 40, 80, 80)).toImage()
    dpr = img.devicePixelRatio()
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y).alpha() > 8 and ((x / dpr - 40) ** 2 + (y / dpr - 40) ** 2) ** 0.5 > radius:
                n += 1
    return n


def test_the_new_look_has_no_glow(overlay):
    for style in ("classic", "new"):
        overlay.set_style(style)
        _at_rest(overlay)
        overlay.set_mode("idle")
        overlay._tick()
    overlay.set_style("classic")
    _at_rest(overlay)
    assert _painted_outside(overlay, 13) > 0, "the classic triangle has its halo"
    overlay.set_style("new")
    _at_rest(overlay)
    assert _painted_outside(overlay, 13) == 0, "the new look paints nothing round the cursor"


def test_the_new_island_does_not_breathe_or_bloom(qapp, monkeypatch):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QImage, QPainter

    from ui.caption import CaptionIsland
    for style, breathes in (("classic", True), ("new", False)):
        cap = CaptionIsland()
        cap.set_style(style)
        cap._state, cap._p, cap._energy = "live", 1.0, 1.0
        scales = set()
        for now in (0.1, 0.5, 0.9, 1.3):
            cap._now = now
            cap._geometry(QRectF(0, 0, 1920, 1040), 0.0, 0.0)
            scales.add(round(cap._scale, 6))
        assert (len(scales) > 1) is breathes, style
        blooms = []
        monkeypatch.setattr(CaptionIsland, "_draw_bloom", lambda self, *a: blooms.append(1))
        img = QImage(1920, 200, QImage.Format.Format_ARGB32)
        p = QPainter(img)
        cap.draw(p)
        p.end()
        assert bool(blooms) is breathes, style
        monkeypatch.undo()
