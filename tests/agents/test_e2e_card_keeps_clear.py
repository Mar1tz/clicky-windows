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


class Clock:
    """ui.flow_bar's time.monotonic, moved by hand."""

    def __init__(self, monkeypatch, t=1000.0):
        import ui.flow_bar as fb
        self.t = t
        monkeypatch.setattr(fb.time, "monotonic", lambda: self.t)


def _body(bar):
    from ui.flow_bar import PAD
    g = bar.card.geometry()
    return QRectF(g.x() + PAD, g.y() + PAD, g.width() - 2 * PAD, g.height() - 2 * PAD)


def test_the_bar_moves_its_card_when_tora_draws_under_it(qapp, monkeypatch):
    from ui.flow_bar import FlowBar
    clock = Clock(monkeypatch)
    bar = FlowBar()
    try:
        bar.show()
        bar.card.show()
        bar._place_card()
        body = _body(bar)
        bar.note_drawing({"kind": "rect", "x1": body.left() + 20, "y1": body.top() + 20,
                          "x2": body.right() - 20, "y2": body.bottom() - 20})
        clock.t += 0.3
        bar._tick()                                        # SP-11: it slides there
        moved = _body(bar)
        assert moved != body and not moved.intersects(body.adjusted(20, 20, -20, -20))
        bar.clear_drawings()
        bar._place_card()
        assert _body(bar).topLeft() == body.topLeft(), "with the drawing gone it is back by the pill"
    finally:
        bar.close()


# -- SP-11: it glides ----------------------------------------------------------------------------------------

def test_off_a_drawing_the_card_slides_and_never_jumps(qapp, monkeypatch):
    from ui.flow_bar import FlowBar
    clock = Clock(monkeypatch)
    bar = FlowBar()
    try:
        bar.show()
        bar.card.show()
        bar._place_card()
        start = bar.card.pos()
        body = _body(bar)
        bar.note_drawing({"kind": "rect", "x1": body.left(), "y1": body.top(),
                          "x2": body.right(), "y2": body.bottom()})
        assert bar.card.pos() == start, "not moved at once"
        _from, end, _t0 = bar._card_glide
        path = []
        for _ in range(5):
            clock.t += 0.06
            bar._tick()
            path.append(bar.card.pos())
        far = abs(end.x() - start.x()) + abs(end.y() - start.y())
        gone = [abs(p.x() - start.x()) + abs(p.y() - start.y()) for p in path]
        assert 0 < gone[0] < far and gone == sorted(gone), "a little way, then further"
        assert path[-1] == end and bar._card_glide is None, "there in a quarter of a second"
    finally:
        bar.close()


def test_the_first_placing_is_not_a_glide(qapp, monkeypatch):
    from ui.flow_bar import FlowBar
    Clock(monkeypatch)
    bar = FlowBar()
    try:
        bar._place_card(glide=True)                        # hidden: nothing to slide from
        assert bar._card_glide is None
    finally:
        bar.close()


# -- SP-12: what is on screen now ---------------------------------------------------------------------------

def test_a_walkthrough_drawing_kept_a_minute_keeps_the_card_off_the_whole_minute(qapp, monkeypatch):
    """The card used to forget a drawing after 20 s (AVOID_FOR_S) and could
    slide back over a walkthrough's arrow that was still there."""
    from ui.flow_bar import FlowBar
    clock = Clock(monkeypatch)
    bar = FlowBar()
    try:
        bar.show()
        bar.card.show()
        bar._place_card()
        body = _body(bar)
        arrow = QRectF(body.left() + 30, body.top() + 30, body.width() - 60, body.height() - 60)
        on_screen = [arrow]
        bar.drawn_now = lambda: list(on_screen)            # FlowBar.watch(overlay) in the app
        for t in (0.0, 30.0, 59.0):
            clock.t = 1000.0 + t
            bar._place_card()
            assert not _body(bar).intersects(arrow), f"still clear at {t:.0f} s"
        on_screen.clear()                                  # the step ended; the overlay rubbed it out
        bar._place_card()
        assert _body(bar).topLeft() == body.topLeft(), "back by the pill"
    finally:
        bar.close()


def test_the_card_reads_the_overlay(qapp):
    from ui.flow_bar import overlay_bounds, point_bounds

    class Overlay:
        def drawn_now(self):
            return [{"kind": "circle", "x": 500, "y": 400, "r": 40}], (1200.0, 300.0)
    rects = overlay_bounds(Overlay())
    assert rects[0].contains(QPointF(500, 400)) and rects[1] == point_bounds(1200.0, 300.0)


def test_the_overlay_says_what_is_drawn_now(qapp, monkeypatch):
    import ui.overlay as ov_mod
    ov = ov_mod.CursorOverlay.__new__(ov_mod.CursorOverlay)
    ov._annotations = [{"kind": "circle", "x": 1, "y": 1, "r": 5, "start": 0.0, "ttl": 10.0},
                       {"kind": "circle", "x": 2, "y": 2, "r": 5, "start": 0.0, "ttl": None}]
    ov._ring = (300.0, 200.0, 26.0)
    monkeypatch.setattr(ov_mod.time, "monotonic", lambda: 50.0)
    shapes, ring = ov.drawn_now()
    assert [sh["x"] for sh in shapes] == [2], "the expired one is gone, the walkthrough's stays"
    assert ring == (300.0, 200.0)


# -- SP-11: the arrowhead is not under the buddy ----------------------------------------------------------------

def test_the_buddy_rests_back_along_the_arrow_not_on_its_head():
    from ui.overlay import REST_BACK_PX, _rest_point
    x, y = _rest_point({"kind": "arrow", "pts": [(100.0, 100.0), (400.0, 500.0)]})
    assert (round(x), round(y)) == (400 - 24, 500 - 32), "40 px back along the arrow"
    assert REST_BACK_PX == 40.0
    sx, sy = _rest_point({"kind": "arrow", "pts": [(100.0, 100.0), (110.0, 100.0)]})
    assert (sx, sy) == (105.0, 100.0), "a short arrow: half way, never past its tail"
    assert _rest_point({"kind": "circle", "x": 50.0, "y": 50.0, "r": 10.0}) is not None
