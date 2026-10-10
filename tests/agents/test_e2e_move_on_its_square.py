"""A move is drawn between the squares Tora names (ai/look_board.py).

On 10 Oct 2026 a user on chess.com asked for their best move four times.
Tora said "knight from g1 to f3" every time and drew the arrow to g3 every
time - the user told her so, and the next arrow went to g3 again. The model
read the board right and put its box a file off. Now the board is found in
the screenshot and the arrow goes from the centre of the square the answer
names to the centre of the square it names.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import gemini_vision, look_board

W, H = 1024, 576
LEFT, TOP, SQ = 300, 40, 60
GREEN, CREAM = (119, 149, 86), (235, 236, 208)


def _board_picture(white_bottom=True, highlight=("e7", "e5"), board=True) -> str:
    """A chess.com-like page after 1.e4 e5, as the app sends it: a JPEG."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), (49, 46, 43))
    d = ImageDraw.Draw(im)
    for i in range(10):                                   # a menu down the side
        d.rectangle([20, 60 + i * 40, 180, 80 + i * 40], fill=(90, 90, 90))
    if board:
        occupied = {f + r for f in "abcdefgh" for r in "1278"} - {"e2", "e7"} | {"e4", "e5"}
        for f in "abcdefgh":
            for r in range(1, 9):
                name = f + str(r)
                c, row = look_board.cell(name, white_bottom)
                x, y = LEFT + c * SQ, TOP + row * SQ
                light = ("abcdefgh".index(f) + r) % 2 == 0
                colour = CREAM if light else GREEN
                if name in highlight:
                    colour = (247, 247, 105) if light else (187, 203, 43)
                d.rectangle([x, y, x + SQ - 1, y + SQ - 1], fill=colour)
                if name in occupied:
                    ink = (250, 250, 250) if r <= 4 else (30, 30, 30)
                    d.ellipse([x + 14, y + 10, x + SQ - 14, y + SQ - 8], fill=ink, outline=(0, 0, 0), width=2)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=75)
    return base64.b64encode(buf.getvalue()).decode()


def _centre(name, white_bottom=True) -> tuple[float, float]:
    c, r = look_board.cell(name, white_bottom)
    return LEFT + (c + 0.5) * SQ, TOP + (r + 0.5) * SQ


def _box(name, white_bottom=True, nudge=(0, 0), half=26) -> list[int]:
    """A model's box round a square, 0-1000, a few pixels out as they are."""
    x, y = _centre(name, white_bottom)
    x, y = x + nudge[0], y + nudge[1]
    return [round((y - half) * 1000 / H), round((x - half) * 1000 / W),
            round((y + half) * 1000 / H), round((x + half) * 1000 / W)]


def _the_10_oct_look(white_bottom=True, answer="Move the knight from g1 to f3 to develop a piece and control the center."):
    """What the deep look returned: the knight's box right, its square g3."""
    return gemini_vision._parse({"answer": answer, "actions": [
        {"label": "Knight to f3", "kind": "move", "box": _box("g1", white_bottom, (3, -2)),
         "to_box": _box("g3", white_bottom)}]}, "gemini-3.1-pro-preview", True)


def _tip(action) -> tuple[float, float]:
    x, y = action.to_center
    return x * W / 1000, y * H / 1000


def _tail(action) -> tuple[float, float]:
    x, y = action.center
    return x * W / 1000, y * H / 1000


def _on(point, name, white_bottom=True) -> bool:
    x, y = _centre(name, white_bottom)
    return abs(point[0] - x) < SQ * 0.15 and abs(point[1] - y) < SQ * 0.15


# -- the squares a sentence names -------------------------------------------------------------------------------

@pytest.mark.parametrize("said, move", [
    ("Move the knight from g1 to f3 to develop a piece and control the center.", ("g1", "f3")),
    ("Knight g1-f3.", ("g1", "f3")),
    ("Push the pawn from e2 to e4.", ("e2", "e4")),
    ("e2e4", ("e2", "e4")),
    ("The knight on g1 goes to f3.", ("g1", "f3")),
    ("Take the pawn on e5 with your knight from f3.", ("f3", "e5")),
    ("Play Nf3, developing the knight.", (None, "f3")),
    ("Bxc4 wins a piece.", (None, "c4")),
    ("exd5 opens the centre.", (None, "d5")),
    ("Castle kingside.", (None, None)),
    ("Click Save.", (None, None)),
])
def test_the_squares_a_move_names(said, move):
    assert look_board.named_move(said) == move


# -- finding the board ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("white_bottom", [True, False])
def test_the_board_is_found_to_the_pixel(white_bottom):
    board = look_board.find_board(look_board._decode(_board_picture(white_bottom)))
    assert board is not None
    assert abs(board.left - LEFT) <= 2 and abs(board.top - TOP) <= 2 and abs(board.size - SQ) <= 0.5


def _grid_picture() -> str:
    """A spreadsheet: lines at an even spacing, but no checker."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    for i in range(0, W, 64):
        d.line([i, 0, i, H], fill=(200, 200, 200))
    for j in range(0, H, 24):
        d.line([0, j, W, j], fill=(200, 200, 200))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=75)
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.parametrize("picture", [lambda: _board_picture(board=False), _grid_picture])
def test_no_board_is_found_where_there_is_none(picture):
    assert look_board.find_board(look_board._decode(picture())) is None


def test_a_picture_that_is_not_one_finds_nothing():
    assert look_board._decode("x") is None


# -- the move, on its squares -----------------------------------------------------------------------------------

@pytest.mark.parametrize("white_bottom", [True, False])
def test_the_10_oct_arrow_goes_to_f3_not_g3(white_bottom, caplog):
    look = _the_10_oct_look(white_bottom)
    with caplog.at_level(logging.INFO, logger="tora.board"):
        note = look_board.place_moves(look.actions, _board_picture(white_bottom), look.answer)
    move = look.actions[0]
    assert _on(_tail(move), "g1", white_bottom) and _on(_tip(move), "f3", white_bottom)
    assert note == "on the board g1→f3 (its boxes said g1→g3)"
    assert move.exact and move.is_move
    assert ("white" if white_bottom else "black") + " at the bottom" in caplog.text


def test_an_end_the_answer_does_not_name_goes_on_the_square_under_the_box():
    look = _the_10_oct_look(answer="Play Nf3, developing the knight.")
    look.actions[0].box = tuple(_box("g1", nudge=(14, 12)))      # well off the knight's middle
    look_board.place_moves(look.actions, _board_picture(), look.answer)
    assert _on(_tail(look.actions[0]), "g1") and _on(_tip(look.actions[0]), "f3")


def test_without_a_board_the_model_boxes_stay():
    look = _the_10_oct_look()
    before = (look.actions[0].box, look.actions[0].to_box)
    assert look_board.place_moves(look.actions, _board_picture(board=False), look.answer) == ""
    assert (look.actions[0].box, look.actions[0].to_box) == before and not look.actions[0].exact


def test_rings_and_points_are_left_alone():
    look = gemini_vision._parse({"answer": "The knight on g1.", "actions": [
        {"label": "g1", "kind": "circle", "box": _box("g1")}]}, "m", True)
    before = look.actions[0].box
    assert look_board.place_moves(look.actions, _board_picture(), look.answer) == ""
    assert look.actions[0].box == before


# -- through the look, as the user sees it ----------------------------------------------------------------------

def _manager(picture):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    drawn = []
    mgr.sig_draw = SimpleNamespace(emit=drawn.append)
    mgr.sig_clear_drawings = SimpleNamespace(emit=lambda: None)
    mgr._drawn = drawn
    mgr._privacy_guard = False
    mgr._activity = lambda what: None
    shot = SimpleNamespace(base64_jpeg=picture)

    async def capture(**_kw):
        return [shot]
    mgr._capture_shots = capture
    mgr._active_shot = lambda screens: shot
    mgr._shape_from_tag = lambda tag: {"tag": tag}
    return mgr


def test_the_user_sees_the_arrow_go_to_f3(monkeypatch, caplog):
    mgr = _manager(_board_picture())

    async def fake_look(*_a, **_kw):
        return _the_10_oct_look()
    monkeypatch.setattr(gemini_vision, "look", fake_look)
    monkeypatch.setattr(cm, "active_window_title", lambda: "Play Chess Online Against the Computer - Chess.com")
    with caplog.at_level(logging.INFO):
        out = asyncio.run(mgr._tool_look_at_screen({"question": "what is my next best move?", "careful": True}))
    (tag,) = [d["tag"] for d in mgr._drawn]
    x0, y0, x1, y1 = [int(v) for v in tag[len("[ARROW:"):-len(":tora]")].replace("->", ",").split(",")]
    assert _on((x0 * W / 1000, y0 * H / 1000), "g1") and _on((x1 * W / 1000, y1 * H / 1000), "f3")
    assert out["highlights"][0]["tag"] == tag, "the voice is told where the arrow really is"
    assert "g1→f3 (its boxes said g1→g3)" in caplog.text


def test_a_board_square_is_not_snapped_to_a_control(monkeypatch):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    monkeypatch.setattr(cm.look_region, "control_chain_at", lambda *a, **k: [((0, 0, 500, 500), "page")])
    monkeypatch.setattr(cm.look_region, "best_control", lambda *a, **k: (0, 0, 500, 500))
    placed = gemini_vision.Action("Nf3", (100, 100, 200, 200), "move", (300, 300, 400, 400), exact=True)
    loose = gemini_vision.Action("Save", (100, 100, 200, 200), "rect")
    monitor = (0, 0, 1000, 1000)
    assert asyncio.run(mgr._snap_to_controls([placed, loose], monitor, None)) == 1
    assert placed.box == (100, 100, 200, 200) and placed.to_box == (300, 300, 400, 400)
    assert loose.box != (100, 100, 200, 200)
