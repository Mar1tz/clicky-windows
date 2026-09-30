"""The buddy cursor's look: the polished triangle (chosen 30 Sep 2026).

The user asked for the cursor to "look tons better" and picked this from
four drawn side by side on their screen: rounded, a light-to-deep blue
gradient, a soft shadow and a quiet glow - not a flat triangle inside three
stacked blue discs.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _render(draw, size=80):
    from PyQt6.QtGui import QImage, QPainter
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    draw(p, size / 2, size / 2)
    p.end()
    return img


def test_the_triangle_is_blue_with_a_gradient_light_at_the_top(qapp):
    from ui import buddy
    img = _render(lambda p, x, y: buddy.draw_pointer(p, x, y, rotation=0.0))
    top, bottom = img.pixelColor(40, 34), img.pixelColor(40, 44)
    assert top.alpha() == bottom.alpha() == 255
    assert top.blue() > 200 and bottom.blue() > 180, "Tora blue"
    assert top.lightness() > bottom.lightness() + 20, "light at the top, deep below"


def test_the_glow_is_soft_not_a_blob(qapp):
    """The old look drew three blue discs out to 17 px at up to a third
    opacity each; this glow fades to nothing."""
    from ui import buddy
    img = _render(lambda p, x, y: buddy.draw_pointer(p, x, y, rotation=0.0, breath=1.0))
    assert img.pixelColor(40 - 15, 40 - 6).alpha() < 40, "faint beside it"
    assert img.pixelColor(40 - 21, 40).alpha() < 8, "gone a little further out"


def test_it_has_a_soft_shadow_underneath(qapp):
    from ui import buddy
    img = _render(lambda p, x, y: buddy.draw_pointer(p, x, y, rotation=0.0, breath=0.0, alpha=1.0))
    below = img.pixelColor(40, 40 + 7)        # just under the bottom edge
    assert below.alpha() > 40 and below.lightness() < 120, "a dark soft edge, not blue haze"


def test_every_state_draws(qapp):
    from ui import buddy
    for draw in (lambda p, x, y: buddy.draw_listening(p, x, y, 0.8, 1.0),
                 lambda p, x, y: buddy.draw_thinking(p, x, y, 2.0),
                 lambda p, x, y: buddy.draw_bubble(p, x - 30, y - 20, "Export")):
        img = _render(draw, 120)
        assert any(img.pixelColor(x, y).alpha() > 200 for x in range(0, 120, 2) for y in range(0, 120, 2))


def test_the_name_bubble_sits_beside_the_buddy(qapp):
    from ui import buddy
    r = buddy.bubble_rect(100, 100, "Export")
    assert r.left() > 100 and r.top() > 100 and r.height() < 40, "down and right of it, one line"


def test_the_overlay_draws_its_buddy_this_way_in_every_state(qapp, monkeypatch):
    from PyQt6.QtGui import QImage, QPainter
    from ui import buddy
    from ui.overlay import MODE_IDLE, MODE_LISTENING, MODE_THINKING, CursorOverlay
    drawn = []
    for name in ("draw_pointer", "draw_listening", "draw_thinking", "draw_bubble"):
        monkeypatch.setattr(buddy, name, lambda *a, _n=name, **k: drawn.append(_n))
    o = CursorOverlay()
    try:
        img = QImage(10, 10, QImage.Format.Format_ARGB32)
        for mode, want in ((MODE_IDLE, "draw_pointer"), (MODE_LISTENING, "draw_listening"),
                           (MODE_THINKING, "draw_thinking")):
            o.set_mode(mode)
            drawn.clear()
            p = QPainter(img)
            o._paint(p)
            p.end()
            assert drawn == [want], mode
        o.set_mode(MODE_IDLE)
        o.point_at(300.0, 200.0, "Export")
        drawn.clear()
        p = QPainter(img)
        o._paint(p)
        p.end()
        assert drawn == ["draw_pointer", "draw_bubble"], "pointing: the buddy and its name"
    finally:
        o._tick_timer.stop()
        o.deleteLater()
