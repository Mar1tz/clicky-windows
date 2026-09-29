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


def _grab(overlay, x, y, w, h):
    from PyQt6.QtCore import QRect
    img = overlay.grab(QRect(int(x - overlay.x()), int(y - overlay.y()), w, h)).toImage()
    return img, img.devicePixelRatio()


def test_the_new_look_has_no_glow(overlay):
    from ui import overlay_paint
    overlay.set_style("classic")
    _at_rest(overlay)
    overlay.set_mode("idle")
    assert _painted_outside(overlay, 13) > 0, "the classic triangle has its halo"
    overlay.set_style("new")
    _at_rest(overlay)
    box = overlay_paint.arrow_box(overlay._display_pos)
    img, dpr = _grab(overlay, box.x() - 30, box.y() - 30, int(box.width()) + 60, int(box.height()) + 60)
    stray = [(x, y) for y in range(img.height()) for x in range(img.width())
             if img.pixelColor(x, y).alpha() > 8
             and not box.adjusted(-1, -1, 1, 1).contains(box.x() - 30 + x / dpr, box.y() - 30 + y / dpr)]
    assert stray == [], "the new look paints nothing outside the arrow itself"


# -- OD-03: the arrow -----------------------------------------------------------------------------------------

def _is_accent(c) -> bool:
    return c.alpha() > 200 and c.blue() > 200 and c.red() < 90 and 100 < c.green() < 170


@pytest.mark.parametrize("where", [(0.31, 0.29), (0.63, 0.57)])
def test_the_tip_lands_on_the_target_pixel(overlay, where):
    # Inside whatever screen this run has: the real one (at its scaling), or
    # the 800 x 800 offscreen one another test module switches Qt to.
    g = overlay.geometry()
    target = (int(g.x() + g.width() * where[0]), int(g.y() + g.height() * where[1]))
    overlay.set_style("new")
    _at_rest(overlay)
    overlay.guide_point(float(target[0]), float(target[1]), "Export")
    overlay._fly_t0 -= 5.0
    overlay._tick()
    overlay._ring = None                              # look at the arrow alone
    assert (overlay._display_pos.x(), overlay._display_pos.y()) == target, "the tip's anchor is the target"
    from ui import overlay_theme as t
    img, dpr = _grab(overlay, target[0] - 10, target[1] - 10, 40, 40)
    # Blue-dominant pixels, the anti-aliased tip included: at 125% scaling the
    # very tip is a blend of the fill and the white outline.
    blue = [((x + 0.5) / dpr - 10, (y + 0.5) / dpr - 10) for y in range(img.height()) for x in range(img.width())
            if img.pixelColor(x, y).alpha() > 100 and img.pixelColor(x, y).blue() > img.pixelColor(x, y).red() + 80]
    tip = min(blue, key=lambda q: q[0] + q[1])
    assert (tip[0] ** 2 + tip[1] ** 2) ** 0.5 <= t.OUTLINE_W, f"the tip is {tip} from the target"
    assert min(q[0] for q in blue) >= -1.0 and min(q[1] for q in blue) >= -1.0, "nothing blue beyond the tip"


def test_the_classic_triangle_does_not_point_with_its_tip(overlay):
    """What OD-03 fixes: the classic triangle's centre lands on the target."""
    overlay.set_style("classic")
    _at_rest(overlay)
    overlay.guide_point(600.0, 300.0, "Export")
    overlay._fly_t0 -= 5.0
    overlay._tick()
    overlay._ring = None
    img, dpr = _grab(overlay, 580, 280, 40, 40)
    blue = [(x / dpr - 20, y / dpr - 20) for y in range(img.height()) for x in range(img.width())
            if img.pixelColor(x, y).alpha() > 200 and img.pixelColor(x, y).blue() > 200]
    assert min(q[1] for q in blue) < -5, "part of it sits well above the target"


def test_new_moves_are_straight_quick_and_do_not_spin(overlay):
    from ui import overlay_theme as t
    overlay.set_style("new")
    overlay.set_slow_mode(False)
    _at_rest(overlay, 100, 100)
    overlay.point_at(1300.0, 100.0, "Save")                      # while Tora explains
    assert t.EXPLAIN_MOVE_MIN_S <= overlay._fly_duration <= t.EXPLAIN_MOVE_MAX_S
    s, e, c = overlay._fly_start_pos, overlay._fly_end_pos, overlay._fly_control
    assert (c.x(), c.y()) == ((s.x() + e.x()) / 2, (s.y() + e.y()) / 2), "a straight line, no arc"
    overlay._fly_t0 -= overlay._fly_duration / 2
    overlay._tick()
    assert overlay._flight_scale == 1.0 and overlay._rotation_deg == pytest.approx(-35.0)
    d = overlay._display_pos
    cross = (e.x() - s.x()) * (d.y() - s.y()) - (e.y() - s.y()) * (d.x() - s.x())
    assert abs(cross) < 1e-6 * (e.x() - s.x()) ** 2 + 1e-3, "on the straight line, not up and over"
    assert s.x() < d.x() < e.x()
    overlay.guide_point(700.0, 500.0, "Export")                  # a walkthrough step
    assert overlay._fly_duration == pytest.approx(t.STEP_MOVE_S)
    overlay._fly_t0 -= 5.0
    overlay._tick()
    overlay.guide_release()
    overlay._tick()
    assert overlay._flight_phase == "returning" and overlay._fly_duration == pytest.approx(t.RETURN_MOVE_S)


def test_the_new_ring_draws_in_and_then_is_still(overlay):
    overlay.set_style("new")
    _at_rest(overlay)
    overlay.guide_point(600.0, 300.0, "Export")
    overlay._fly_t0 -= 5.0
    overlay._tick()
    overlay._arrived_at -= 1.0                                   # well after landing
    first, dpr = _grab(overlay, 570, 270, 60, 60)
    overlay._tick()
    second, _ = _grab(overlay, 570, 270, 60, 60)
    ring = [(x, y) for y in range(first.height()) for x in range(first.width())
            if _is_accent(first.pixelColor(x, y)) and ((x / dpr - 30) ** 2 + (y / dpr - 30) ** 2) ** 0.5 > 12]
    assert ring, "a ring round the target"
    assert first == second, "and it does not pulse"


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


# -- OD-04: the step chip ------------------------------------------------------------------------------------

@pytest.mark.parametrize("instruction, target, current, total, shown", [
    ("Click Export.", "Export", 2, 5, "2/5 Click Export"),
    ("Double-click the project folder.", "Projects", 1, 3, "1/3 Double-click Projects"),
    ("Right-click the photo.", "Photo", 2, 3, "2/3 Right-click Photo"),
    ("Type your name in the Name box.", "Name", 3, 5, "3/5 Type your name"),
    ('Type "hello world" into the box.', "Message", 3, 5, "3/5 Type hello world"),
    ("Press Ctrl+S to save.", "Save", 4, 5, "4/5 Press Ctrl+S"),
    ("Press ctrl + shift + n for a new window.", "", 1, 2, "1/2 Press Ctrl+Shift+N"),
    ("Hit Enter.", "", 5, 5, "5/5 Press Enter"),
    ("Choose Save as from the menu.", "Save as", 2, 6, "2/6 Choose Save as"),
    ("Select the Export option.", "Export", 1, 2, "1/2 Choose Export"),
    ("Scroll down to the bottom.", "", 3, 7, "3/7 Scroll down"),
    ("Open the File menu.", "File", 1, -1, "1 Open File"),
    ("", "Export", 0, 0, "Export"),
    ("Look at the chart.", "Chart", 4, 5, "4/5 Chart"),
])
def test_the_chip_says_the_action_and_the_thing(instruction, target, current, total, shown):
    from ui.overlay_chip import chip_for
    assert chip_for(instruction, target, current=current, total=total).text == shown


def test_the_chip_never_shows_a_password_and_stays_short():
    from ui import overlay_theme as t
    from ui.overlay_chip import chip_for
    c = chip_for('Type "hunter2" as your password.', "Password", current=2, total=4)
    assert c.text == "2/4 Type your password" and "hunter2" not in c.text
    long = chip_for("Click the button labelled Download all attachments as a zip file.",
                    "Download all attachments as a zip file", current=2, total=5)
    assert len(" ".join(w for _k, w in long.parts)) <= t.CHIP_MAX_CHARS and long.text.endswith("…")
    assert not chip_for("", "", current=0, total=0), "nothing known: no chip at all"


def _new_step(overlay, instruction="Click Export.", label="Export", at=(600.0, 300.0)):
    overlay.set_style("new")
    _at_rest(overlay)
    overlay.set_guide_progress(2, 5, instruction)
    overlay.guide_point(at[0], at[1], label)
    overlay._fly_t0 -= 5.0
    overlay._tick()
    overlay._arrived_at -= 1.0


def test_only_one_label_is_drawn_in_the_new_look(overlay, monkeypatch):
    from ui import overlay_paint
    drawn = []
    monkeypatch.setattr(type(overlay), "_draw_bubble", lambda self, *a: drawn.append("bubble"))
    monkeypatch.setattr(type(overlay), "_draw_island", lambda self, *a: drawn.append("card"))
    real_chip = overlay_paint.draw_chip
    monkeypatch.setattr(overlay_paint, "draw_chip", lambda *a, **k: (drawn.append("chip"), real_chip(*a, **k)))
    _new_step(overlay)
    _grab(overlay, 500, 250, 300, 120)
    assert drawn == ["chip"]
    assert overlay._chip.text == "2/5 Click Export"


def test_no_random_phrase_when_the_name_is_unknown(overlay):
    from ui.overlay import POINTER_PHRASES
    overlay.set_style("new")
    _at_rest(overlay)
    overlay.point_at(600.0, 300.0, "")
    assert overlay._bubble_text not in POINTER_PHRASES and not overlay._chip
    overlay.set_style("classic")
    overlay.point_at(600.0, 300.0, "")
    assert overlay._bubble_text in POINTER_PHRASES, "the classic look keeps its phrases until OD-09"


def test_the_chip_dims_while_you_talk(overlay):
    _new_step(overlay)
    assert overlay._chip_alpha() == pytest.approx(1.0)
    overlay.set_mode("listening")
    from ui import overlay_theme as t
    assert overlay._chip_alpha() == pytest.approx(t.DIM_WHILE_TALKING)


# -- OD-05: where the chip goes -------------------------------------------------------------------------------

LAYOUTS = {
    "one laptop at 125%": [(0, 0, 1536, 824)],
    "two side by side, one left of primary": [(-1920, 0, 1920, 1040), (0, 0, 2560, 1400)],
    "a small screen and a portrait one above it": [(0, 0, 1280, 680), (1280, -200, 1080, 1880)],
}


def _grid(r):
    xs = [r.left() + 2, r.left() + 40, r.center().x(), r.right() - 40, r.right() - 2]
    ys = [r.top() + 2, r.top() + 30, r.center().y(), r.bottom() - 30, r.bottom() - 2]
    return [(x, y) for x in xs for y in ys]


@pytest.mark.parametrize("layout", list(LAYOUTS))
@pytest.mark.parametrize("width", [70.0, 150.0, 290.0])
def test_the_chip_never_covers_the_target_and_stays_on_its_screen(layout, width):
    from PyQt6.QtCore import QPointF, QRectF, QSizeF

    from ui import overlay_paint, overlay_theme as t
    from ui.overlay_chip import EDGE, place_chip, target_ring
    for sx, sy, sw, sh in LAYOUTS[layout]:
        screen = QRectF(sx, sy, sw, sh)
        island = QRectF(screen.center().x() - 220, screen.top() + t.ISLAND_TOP, 440, t.ISLAND_H2)
        for x, y in _grid(screen):
            tip = QPointF(x, y)
            ring = target_ring(tip)
            arrow = overlay_paint.arrow_box(tip)
            chip = place_chip(ring, QSizeF(width, t.CHIP_H), screen, [island, arrow])
            where = f"{layout}: target {x:.0f},{y:.0f}, chip {chip}"
            assert screen.adjusted(EDGE, EDGE, -EDGE, -EDGE).contains(chip), "off its screen - " + where
            assert not chip.intersects(ring), "over the target - " + where
            assert not chip.intersects(island), "over the island - " + where


def test_the_chip_prefers_the_right_then_the_left():
    from PyQt6.QtCore import QPointF, QRectF, QSizeF

    from ui.overlay_chip import place_chip, target_ring
    screen = QRectF(0, 0, 1920, 1040)
    size = QSizeF(150, 28)
    middle = target_ring(QPointF(900, 500))
    assert place_chip(middle, size, screen).left() > middle.right()
    edge = target_ring(QPointF(1900, 500))
    assert place_chip(edge, size, screen).right() < edge.left()


def test_the_targets_bounds_become_the_new_ring_and_the_chip_keeps_clear(overlay):
    from PyQt6.QtCore import QRectF
    _new_step(overlay)
    button = QRectF(555, 286, 90, 28)                  # "Export", as Windows reports it
    overlay.add_shape({"kind": "rect", "color": "blue", "role": "target",
                       "x1": 551, "y1": 282, "x2": 649, "y2": 318})     # as _highlight pads it
    assert overlay._annotations == [], "the new look draws its own ring, not a second one"
    assert overlay._target_bounds == button
    assert not overlay._chip_rect.intersects(button.adjusted(-4, -4, 4, 4)), "the chip clears the whole button"
    overlay.set_style("classic")
    overlay.add_shape({"kind": "rect", "color": "blue", "role": "target",
                       "x1": 551, "y1": 282, "x2": 649, "y2": 318})
    assert len(overlay._annotations) == 1, "classic draws it as before"


def test_the_highlight_is_marked_as_the_target():
    import inspect

    from companion_manager import CompanionManager
    src = inspect.getsource(CompanionManager._highlight)
    assert src.count('"role": "target"') == 2


# -- OD-06: the island is a fixed bar ------------------------------------------------------------------------

LONG_ANSWER = ("The export button is in the File menu, second item from the top. Choose it, then pick where "
               "the file should go and press Save.")


def _speak(cap, text, seconds=6.0, width=1920):
    """Feed an answer the way the overlay does and tick it for `seconds`;
    the pill's size on every frame it can be seen."""
    import time as _time

    from PyQt6.QtCore import QRectF
    cap.on_state("SPEAKING")
    for word in text.split(" "):
        cap.push(word + " ")
    cap.finish()
    cap.set_screen_width(width)
    cap.needs_screen = False
    screen = QRectF(0, 0, width, 1040)
    t0 = _time.monotonic()
    sizes = []
    for i in range(int(seconds * 60)):
        cap.tick(t0 + i / 60.0, screen, 0.0, 0.0)
        if cap._alpha > 0.05:
            sizes.append((round(cap._pill.width()), round(cap._pill.height())))
    return sizes


def test_the_new_island_is_one_size_from_the_first_word_to_the_last(qapp):
    from ui import overlay_theme as t
    from ui.caption import CaptionIsland
    new = CaptionIsland()
    new.set_style("new")
    sizes = _speak(new, LONG_ANSWER)
    assert sizes and set(sizes) == {(int(t.ISLAND_W_MAX), int(t.ISLAND_H2))}, "it never grows"
    classic = CaptionIsland()
    assert len(set(_speak(classic, LONG_ANSWER))) > 3, "the classic pill grows word by word"


def test_a_short_answer_sits_in_the_middle_of_the_bar(qapp):
    from PyQt6.QtGui import QImage, QPainter

    from ui import overlay_theme as t
    from ui.caption import CaptionIsland
    cap = CaptionIsland()
    cap.set_style("new")
    # short enough for one line in any font (offscreen Qt's fallback is twice Segoe UI's width)
    _speak(cap, "It's in File.", seconds=3.0)
    assert len(cap._lines) == 1
    img = QImage(1920, 120, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    cap.draw(p)
    p.end()
    pill = cap._pill
    left = int(pill.x() + t.ISLAND_PAD_X + t.ISLAND_GLYPH_W + t.ISLAND_GLYPH_GAP)
    rows = [y for y in range(int(pill.top()), int(pill.bottom()))
            if any(img.pixelColor(x, y).lightness() > 170 for x in range(left, int(pill.right()) - 10, 2))]
    assert rows, "no text found in the bar"
    middle = (rows[0] + rows[-1]) / 2
    assert abs(middle - pill.center().y()) <= 3, f"text centred at {middle}, the bar at {pill.center().y()}"


def test_a_small_screen_gets_a_narrower_bar(qapp):
    from ui.caption import CaptionIsland
    cap = CaptionIsland()
    cap.set_style("new")
    assert set(_speak(cap, "Sure, it's in the File menu.", seconds=1.0, width=600)) == {(360, 58)}


def test_a_spaced_dash_is_kept_and_a_bullet_is_not(qapp):
    from ui.caption import CaptionIsland
    cap = CaptionIsland()
    cap.set_style("new")
    _speak(cap, "- Sure - it's in the File menu.", seconds=2.0)
    words = " ".join(ln.text for ln in cap._lines)
    assert words == "Sure – it's in the File menu."
