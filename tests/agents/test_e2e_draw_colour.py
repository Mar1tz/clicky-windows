"""What Tora draws wears her colour (4 Oct 2026).

The first launch lets the user pick Tora's colour (Make her yours: blue,
sky, violet, rose, teal, amber). The cursor followed it; everything she drew
on the screen - arrows, rings, boxes, underlines, labels, the rings
look_at_screen leaves, the name bubble beside her - stayed the old fixed
blue, because each drawing was labelled "blue" and "blue" meant #3380FF.

Now a drawing with no colour, "tora" or "blue" is painted in her colour,
worked out when it is painted - so changing her colour recolours what is
already on screen. A colour named because it means something (red for a
mistake, green for what is right) is still that colour. No network.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")

ROOT = Path(__file__).resolve().parents[2]
VIOLET, AMBER = "#8C7CFF", "#F29B38"


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _blue_again():
    """Her colour is the overlay's (module-wide); every test starts and ends blue."""
    from ui import overlay, tokens
    overlay.set_cursor_colour(tokens.cursor_hex("blue"))
    yield
    overlay.set_cursor_colour(tokens.cursor_hex("blue"))


# -- the colour a drawing is painted in ---------------------------------------------------------------------------

def test_a_drawing_wears_her_colour_unless_it_names_one(qapp):
    from ui import overlay
    overlay.set_cursor_colour(VIOLET)
    for name in (None, "", "tora", "blue", "TORA", "nonsense"):
        assert overlay.annotation_colour(name).name() == VIOLET.lower(), repr(name)
    assert overlay.annotation_colour("red").name() == "#ff4555", "red still means a mistake"
    assert overlay.annotation_colour("green").name() == "#2ecc71"


def test_every_colour_on_the_list_works(qapp):
    from ui import overlay, tokens
    for key, _name, hex_colour in tokens.CURSOR_COLOURS:
        overlay.set_cursor_colour(tokens.cursor_hex(key))
        assert overlay.annotation_colour(None).name() == hex_colour.lower(), key


def _painted_colours(o, monkeypatch):
    """The colour each drawing on the overlay is painted in, right now."""
    seen = []
    monkeypatch.setattr(o, "_paint_stroke_path",
                        lambda _p, _pts, _closed, _u, col, **_k: seen.append(col.name()))
    monkeypatch.setattr(o, "_paint_circle", lambda _p, _ann, _u, col, _a: seen.append(col.name()))
    monkeypatch.setattr(o, "_paint_text", lambda _p, _ann, _u, col, _a: seen.append(col.name()))
    o._draw_annotations(None)
    return seen


def test_the_overlay_paints_drawings_in_her_colour_and_recolours_them(qapp, monkeypatch):
    from ui.overlay import CursorOverlay, set_cursor_colour
    o = CursorOverlay()
    try:
        set_cursor_colour(VIOLET)
        long_ago = time.monotonic() - 1.0
        for shape in ({"kind": "arrow", "pts": [(10, 10), (90, 90)]},                     # no colour
                      {"kind": "circle", "x": 50, "y": 50, "r": 20, "color": "blue"},       # the old default
                      {"kind": "text", "x": 5, "y": 5, "text": "here", "color": "tora"},
                      {"kind": "underline", "x": 0, "y": 60, "w": 80, "color": "red"}):     # meaning
            o.add_shape(shape)
        for ann in o._annotations:
            ann["start"], ann["dur"], ann["ttl"] = long_ago, 0.5, None
        assert _painted_colours(o, monkeypatch) == ["#8c7cff", "#8c7cff", "#8c7cff", "#ff4555"]
        set_cursor_colour(AMBER)                                   # picked again, mid-drawing
        assert _painted_colours(o, monkeypatch) == ["#f29b38", "#f29b38", "#f29b38", "#ff4555"]
    finally:
        o.close()


def test_the_name_bubble_beside_her_is_her_colour(qapp):
    from PyQt6.QtGui import QColor, QImage, QPainter
    from ui import buddy

    def bubble_middle(hex_colour):
        buddy.set_colour(hex_colour)
        img = QImage(240, 120, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        buddy.draw_bubble(p, 40.0, 30.0, "Export")
        p.end()
        r = buddy.bubble_rect(40.0, 30.0, "Export")
        # Beside the label, inside the pill: its fill, not the white text.
        return QColor(img.pixel(int(r.left() + 6), int(r.center().y())))

    try:
        amber = bubble_middle(AMBER)
        assert amber.red() > 200 and amber.blue() < 120, f"amber bubble, got {amber.name()}"
        blue = bubble_middle("#3380FF")
        assert blue.blue() > 200 and blue.red() < 120, f"the blue one is still blue, got {blue.name()}"
    finally:
        buddy.set_colour("#3380FF")


# -- what Tora draws is labelled hers -----------------------------------------------------------------------------

def _bare_manager():
    from companion_manager import CompanionManager
    m = CompanionManager.__new__(CompanionManager)
    m._active_geometry = lambda _i=1: None
    m._shot = lambda _i=1: None
    m._figures_ctx = []
    return m


def test_her_drawing_tags_are_hers_unless_they_name_a_colour():
    m = _bare_manager()
    shapes = m._extract_shapes("[ARROW:100,100->200,200] [CIRCLE:500,300,80:here] "
                               "[RECT:10,10,90,90] [CIRCLE:400,600,50:wrong:red]")
    assert [s["color"] for s in shapes] == ["tora", "tora", "tora", "red"]


def test_look_at_screens_rings_are_hers():
    from ai import gemini_vision
    for kind in ("rect", "underline", "point"):
        tag = gemini_vision.Action(kind=kind, label="Save", box=(300, 120, 360, 420)).as_tag()
        assert tag.endswith(":tora]") and ":blue]" not in tag, tag


def test_found_elements_are_highlighted_in_her_colour():
    src = (ROOT / "companion_manager.py").read_text(encoding="utf-8")
    assert '"kind": "rect", "color": "tora"' in src and '"kind": "circle", "color": "tora"' in src
    assert 'color or "blue"' not in src and '"color": "blue"' not in src


def test_tora_is_told_to_leave_the_colour_to_the_user():
    """Both routes - the live voice's draw tool and the classic prompt - say
    her drawings wear the user's colour, and to name one only for meaning."""
    import re
    live = (ROOT / "ai" / "gemini_realtime.py").read_text(encoding="utf-8")
    live = re.sub(r'"\s*\n\s*"', "", live)                 # the description's string pieces, joined
    assert "Leave the trailing :color off: your drawings wear your own colour, " \
           "the one the user picked for you." in live
    assert "red for a mistake" in live and "Example: [CIRCLE:500,300,80:here]" in live
    classic = (ROOT / "config.py").read_text(encoding="utf-8")
    assert "Colors: leave :color off - your drawings wear your own colour" in classic
    assert "(default blue)" not in classic
