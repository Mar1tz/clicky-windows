"""The top island is a black notch of one size (2026-09-30).

The user hated the old pill growing as Tora spoke, and chose this from the
iPhone's Dynamic Island and the notch apps for Mac and Windows: pure black,
flush with the top of the screen, one line that rolls up into the edge.
These tests hold it to that.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("PyQt6")

LONG = ("The export button is in the File menu, second item from the top. Choose it, then pick where "
        "the file should go and press Save.")
SCREEN_W, SCREEN_H = 1920, 1040


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _island():
    from ui.caption import CaptionIsland
    return CaptionIsland()


def _say(cap, text):
    cap.on_state("SPEAKING")
    for word in text.split(" "):
        cap.push(word + " ")
    cap.finish()


def _run(cap, t0, seconds, width=SCREEN_W):
    """Tick it at 60 fps the way the overlay does; (time, state, rect) of every frame."""
    from PyQt6.QtCore import QRectF
    seen = []
    for i in range(int(seconds * 60)):
        now = t0 + i / 60.0
        if cap.needs_screen:
            cap.set_screen_width(width)
            cap.needs_screen = False
        if cap.active():
            cap.tick(now, QRectF(0, 0, width, SCREEN_H), 0.0, 0.0)
        seen.append((now, cap._state, QRectF(cap._pill) if cap._alpha > 0 else None))
    return seen


def test_it_is_one_size_from_the_first_word_to_the_last(qapp):
    from ui.caption import CAP_H, CAP_W
    cap = _island()
    _say(cap, LONG)
    seen = _run(cap, time.monotonic(), 6.0)
    sizes = {(round(r.width()), round(r.height())) for _t, _s, r in seen if r is not None}
    assert sizes == {(int(CAP_W), int(CAP_H))}, "it never grows"


def test_a_small_screen_gets_a_narrower_notch(qapp):
    from ui.caption import CAP_H
    cap = _island()
    _say(cap, "Sure, it's in the File menu.")
    seen = _run(cap, time.monotonic(), 1.0, width=600)
    assert {(round(r.width()), round(r.height())) for _t, _s, r in seen if r} == {(360, int(CAP_H))}


def test_it_comes_out_of_the_top_edge_and_never_leaves_it(qapp):
    cap = _island()
    _say(cap, "Sure, it's in the File menu.")
    tops = [r.top() for _t, _s, r in _run(cap, time.monotonic(), 1.0) if r is not None]
    assert tops[0] < 0, "it starts up inside the edge"
    assert tops == sorted(tops) and max(tops) == 0.0, "down to the edge, never past it: no bounce"
    assert cap._pill.center().x() == SCREEN_W / 2, "in the middle of the screen"


def test_a_finished_line_rolls_up_and_one_line_shows(qapp):
    cap = _island()
    _say(cap, LONG)
    rolled, most = False, 0
    t0 = time.monotonic()
    for i in range(360):
        _run(cap, t0 + i / 60.0, 1 / 60.0)
        rolled = rolled or bool(cap._out_line)
        most = max(most, len(cap._lines))
    assert rolled and most == 1
    assert cap._lines[0].text.endswith("Save."), "the last words are the ones showing"


def test_it_stays_a_moment_then_slides_up_quickly(qapp):
    from ui.caption import CAP_OUT_S
    cap = _island()
    _say(cap, "Sure, it's saved.")
    t0 = time.monotonic()
    _run(cap, t0, 1.0)
    cap.on_state("IDLE")
    seen = _run(cap, t0 + 1.0, 5.0)
    lingered = [t for t, s, _r in seen if s == "linger"]
    leaving = [t for t, s, _r in seen if s == "exit"]
    gone = next(t for t, s, _r in seen if s == "hidden")
    assert lingered[-1] - lingered[0] >= 1.5, "long enough to finish reading"
    assert gone - leaving[0] <= CAP_OUT_S + 0.02


def test_when_you_talk_it_goes_at_once(qapp):
    from ui.caption import CAP_OUT_S_FAST
    cap = _island()
    _say(cap, "Sure, it's in the File menu.")
    t0 = time.monotonic()
    _run(cap, t0, 1.5)
    cap.on_state("LISTENING")
    seen = _run(cap, t0 + 1.5, 1.0)
    gone = next(t for t, s, _r in seen if s == "hidden")
    assert gone - (t0 + 1.5) <= CAP_OUT_S_FAST + 0.02


def test_it_is_pure_black_with_no_glow_and_meets_the_edge_with_a_curve(qapp):
    from PyQt6.QtGui import QColor, QImage, QPainter
    cap = _island()
    _say(cap, "Sure, it's in the File menu.")
    _run(cap, time.monotonic(), 2.0)
    backdrop = QColor("#9CC7F2")
    img = QImage(SCREEN_W, 120, QImage.Format.Format_ARGB32)
    img.fill(backdrop)
    p = QPainter(img)
    cap.draw(p)
    p.end()
    r = cap._pill
    at = lambda x, y: img.pixelColor(int(x), int(y)).rgb()  # noqa: E731
    assert at(r.right() - 6, r.center().y()) == QColor(0, 0, 0).rgb(), "pure black"
    assert at(r.center().x(), r.bottom() + 3) == backdrop.rgb(), "no shadow or glow below it"
    assert at(r.right() + 12, r.center().y()) == backdrop.rgb(), "or beside it"
    assert at(r.left() - 2, 0) == QColor(0, 0, 0).rgb(), "it curves out into the top edge"
    assert at(r.left() - 12, 0) == backdrop.rgb()


def test_the_words_are_white_and_in_the_middle(qapp):
    from PyQt6.QtGui import QColor, QImage, QPainter
    cap = _island()
    _say(cap, "It's in File.")
    _run(cap, time.monotonic(), 2.0)
    img = QImage(SCREEN_W, 120, QImage.Format.Format_ARGB32)
    img.fill(QColor("#9CC7F2"))
    p = QPainter(img)
    cap.draw(p)
    p.end()
    r = cap._pill
    from ui.caption import CAP_IND_GAP, CAP_IND_W, CAP_PAD_X
    left = int(r.left() + CAP_PAD_X + CAP_IND_W + CAP_IND_GAP)
    rows = [y for y in range(int(r.top()), int(r.bottom()))
            if any(img.pixelColor(x, y).lightness() > 200 for x in range(left, int(r.right()) - 10))]
    assert rows, "white words"
    assert abs((rows[0] + rows[-1]) / 2 - r.center().y()) <= 3


def test_a_spaced_dash_is_kept_and_a_bullet_is_not(qapp):
    cap = _island()
    _say(cap, "- Sure - it's here.")               # one line in any font
    _run(cap, time.monotonic(), 2.0)
    assert cap._lines[0].text == "Sure – it's here."
