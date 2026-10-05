"""SN-09 (docs/SHOW_THE_NEXT_MOVE.md): the answer card stays on screen and
off what Tora drew.

In the wave-1 demo the card, docked at the bottom, covered the board's
first rank - the knight her own arrow started from - and a user's photo
showed the same. The card now goes where it covers nothing she drew or
pointed at, and is placed on the screen the pill is actually on.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QPointF, QRect, QRectF  # noqa: E402

GEO = QRect(0, 0, 1920, 1040)
CW, CH = 460.0, 182.0


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _inside(x, y):
    return GEO.left() + 8 <= x and x + CW <= GEO.right() - 7 and GEO.top() + 8 <= y and y + CH <= GEO.bottom() - 7


def _covers(x, y, rect: QRectF) -> bool:
    return QRectF(x, y, CW, CH).intersects(rect)


@pytest.mark.parametrize("edge, pill", [
    ("bottom", QRectF(904, 990, 112, 38)), ("bottom", QRectF(5, 990, 112, 38)), ("bottom", QRectF(1800, 990, 112, 38)),
    ("top", QRectF(904, 12, 112, 38)), ("left", QRectF(12, 500, 38, 112)), ("right", QRectF(1870, 5, 38, 112)),
])
def test_the_card_is_always_on_the_screen(qapp, edge, pill):
    from ui.flow_bar import place_card
    assert _inside(*place_card(edge, pill, GEO, CW, CH))


def test_with_nothing_drawn_it_sits_by_the_pill(qapp):
    from ui.flow_bar import place_card
    x, y = place_card("bottom", QRectF(904, 990, 112, 38), GEO, CW, CH)
    assert (x, y) == (960 - CW / 2, 990 - 12 - CH)


def test_it_moves_off_what_tora_drew(qapp):
    from ui.flow_bar import place_card
    pill = QRectF(904, 990, 112, 38)
    usual = place_card("bottom", pill, GEO, CW, CH)
    arrow = QRectF(700, 720, 300, 120)                    # under the usual place
    x, y = place_card("bottom", pill, GEO, CW, CH, (arrow,))
    assert (x, y) != usual and not _covers(x, y, arrow) and _inside(x, y)


def test_when_everything_is_covered_it_covers_least(qapp):
    from ui.flow_bar import place_card
    pill = QRectF(904, 990, 112, 38)
    big = QRectF(0, 0, 1920, 1040)
    corner = QRectF(1400, 780, 500, 240)
    x, y = place_card("bottom", pill, GEO, CW, CH, (big, corner))
    assert not QRectF(x, y, CW, CH).intersects(corner) or (x, y) == place_card("bottom", pill, GEO, CW, CH)


@pytest.mark.parametrize("shape, contains", [
    ({"kind": "arrow", "pts": [(100, 900), (300, 700)]}, (200, 800)),
    ({"kind": "circle", "x": 500, "y": 500, "r": 40}, (530, 470)),
    ({"kind": "rect", "x1": 900, "y1": 300, "x2": 700, "y2": 400}, (800, 350)),
    ({"kind": "text", "x": 50, "y": 60, "text": "x"}, (100, 50)),
])
def test_where_a_drawing_is(qapp, shape, contains):
    from ui.flow_bar import shape_bounds
    assert shape_bounds(shape).contains(QPointF(*contains))
    assert shape_bounds({"kind": "nonsense"}) is None and shape_bounds({"kind": "circle"}) is None


def test_the_bar_moves_its_card_when_tora_draws_under_it(qapp):
    from ui.flow_bar import PAD, FlowBar
    bar = FlowBar()
    try:
        bar.show()
        bar.card.show()
        bar._place_card()
        g = bar.card.geometry()
        body = QRectF(g.x() + PAD, g.y() + PAD, g.width() - 2 * PAD, g.height() - 2 * PAD)
        bar.note_drawing({"kind": "rect", "x1": body.left() + 20, "y1": body.top() + 20,
                          "x2": body.right() - 20, "y2": body.bottom() - 20})
        g2 = bar.card.geometry()
        moved = QRectF(g2.x() + PAD, g2.y() + PAD, g2.width() - 2 * PAD, g2.height() - 2 * PAD)
        assert moved != body and not moved.intersects(body.adjusted(20, 20, -20, -20))
        bar.clear_drawings()
        bar._place_card()
        g3 = bar.card.geometry()
        assert (g3.x(), g3.y()) == (g.x(), g.y()), "with the drawing gone it is back by the pill"
    finally:
        bar.close()
