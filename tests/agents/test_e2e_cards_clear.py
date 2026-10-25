"""Cards that never cover the target (PT-13, the Massive Direction Plan).

The name of what the arrow is on sits in the nearest clear space beside
the mark round it - right, below, left or above, on the monitor - with a
short connector, not on top of the target. And one mark is active at a
time: a walkthrough's next step, or the next thing named, takes the last
one's place.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtWidgets import QApplication

from ui.overlay import CursorOverlay


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def ov(qapp):
    o = CursorOverlay()
    yield o
    o.close()


def _mark(ov, x, y, w=160, h=36, label="Search box"):
    ov.point_at(x, y, label)
    ov.add_shape({"kind": "rect", "mark": True, "x1": x - w / 2, "y1": y - h / 2,
                  "x2": x + w / 2, "y2": y + h / 2})


def test_the_name_sits_beside_the_target_not_on_it(ov):
    _mark(ov, 400.0, 300.0)
    top_left, anchor = ov._bubble_place("Search box")
    box = QRectF(320, 282, 160, 36)
    from ui import buddy
    w, h = buddy.bubble_size("Search box")
    assert not QRectF(top_left.x(), top_left.y(), w, h).intersects(box)
    assert anchor.x() == pytest.approx(box.right()), "a connector from the mark's edge"


def test_one_mark_at_a_time(ov):
    _mark(ov, 400.0, 300.0)
    _mark(ov, 600.0, 420.0, 90, 30, "Save")
    marks = [a for a in ov._annotations if a.get("mark")]
    assert len(marks) == 1 and marks[0]["x1"] == 555
    ov.add_shape({"kind": "line", "pts": [(0, 0), (50, 50)]})      # a lesson's stroke is not a mark
    assert len(ov._annotations) == 2


def test_with_no_mark_it_sits_by_the_arrow_as_before(ov):
    ov._annotations = []
    ov.point_at(400.0, 300.0, "Save")
    assert ov._bubble_place("Save") is None
