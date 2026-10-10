"""In a chess game the move is the engine's, on the position the screen shows
(ai/look_chess.py).

10 Oct 2026, chess.com, after 1.e4 e5 2.Nf3 d5 3.Nc3 dxe4: asked for the
best move, Tora said "Knight from f3 to e4, to capture the pawn" and drew
it - no knight on f3 can go there. Now the look reads the game (its move
list, the board as a FEN), the reading is checked against the screenshot
square by square, and the engine on the PC picks a legal move.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import gemini, gemini_vision, look_board, look_chess
from specialists.chess import engine

W, H = 1024, 576
LEFT, TOP, SQ = 300, 40, 60
GREEN, CREAM = (119, 149, 86), (235, 236, 208)
MOVES_10_OCT = ["1. e4", "e5", "2. Nf3", "d5", "3. Nc3", "dxe4"]
FEN_10_OCT = "rnbqkbnr/ppp2ppp/8/4p3/4p3/2N2N2/PPPP1PPP/R1BQKB1R"


@pytest.fixture(autouse=True)
def _quick_engine(monkeypatch):
    monkeypatch.setattr(look_chess, "ENGINE_S", 0.3)


def _picture(placement=FEN_10_OCT, white_bottom=True, lit=("d5", "e4"), board=True) -> str:
    """The game as chess.com draws it, sent as the app sends it: a JPEG.
    `lit` is the last move, tinted."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), (49, 46, 43))
    d = ImageDraw.Draw(im)
    for i in range(10):
        d.rectangle([20, 60 + i * 40, 180, 80 + i * 40], fill=(90, 90, 90))
    if board:
        cells = look_chess._cells(placement)
        for i, piece in enumerate(cells):
            name = "abcdefgh"[i % 8] + str(8 - i // 8)
            c, r = look_board.cell(name, white_bottom)
            x, y = LEFT + c * SQ, TOP + r * SQ
            light = ("abcdefgh".index(name[0]) + int(name[1])) % 2 == 0
            colour = CREAM if light else GREEN
            if name in lit:
                colour = (247, 247, 105) if light else (187, 203, 43)
            d.rectangle([x, y, x + SQ - 1, y + SQ - 1], fill=colour)
            if piece != ".":
                ink = (250, 250, 250) if piece.isupper() else (70, 68, 66)
                d.ellipse([x + 14, y + 10, x + SQ - 14, y + SQ - 8], fill=ink, outline=(20, 20, 20), width=2)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=75)
    return base64.b64encode(buf.getvalue()).decode()


def _look(chess, answer="Knight from f3 to e4, to capture the pawn."):
    """What the deep look returned on 10 Oct - its own (illegal) move - and
    now its reading of the game."""
    return gemini_vision._parse({"answer": answer, "chess": chess, "actions": [
        {"label": "Ne4", "kind": "move", "box": [700, 600, 790, 650], "to_box": [600, 550, 690, 600]}]},
        "gemini-3.1-pro-preview", True)


def _squares(action, white_bottom=True) -> tuple[str, str]:
    """The squares an arrow is drawn from and to, read off the picture."""
    def name(point):
        c = int((point[0] * W / 1000 - LEFT) // SQ)
        r = int((point[1] * H / 1000 - TOP) // SQ)
        return look_board.name_of(c, r, white_bottom)
    return name(action.center), name(action.to_center)


def _legal(placement_or_moves, frm, to) -> bool:
    pos = (look_chess._replay(look_chess.moves_of(placement_or_moves))[0] if isinstance(placement_or_moves, list)
           else engine.Position.from_fen(placement_or_moves))
    return any(engine.name_of(m.frm) == frm and engine.name_of(m.to) == to for m in pos.legal_moves())


# -- reading a move list ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, moves", [
    (["1. e4", "e5", "2. Nf3"], ["e4", "e5", "Nf3"]),
    (["1.e4 e5 2.Nf3 Nc6"], ["e4", "e5", "Nf3", "Nc6"]),
    (["e4", "e5", "Nf3", "1-0"], ["e4", "e5", "Nf3"]),
    (["12...", "O-O"], ["O-O"]),
    (None, []),
])
def test_a_move_list_as_a_model_writes_it(raw, moves):
    assert look_chess.moves_of(raw) == moves


# -- the 10 Oct position ----------------------------------------------------------------------------------------

def test_the_10_oct_move_is_the_engines_and_legal(caplog):
    with caplog.at_level(logging.INFO, logger="tora.chess"):
        played = look_chess.play(_look({"moves": MOVES_10_OCT, "fen": FEN_10_OCT, "white_at_bottom": True}),
                                 _picture())
    assert played is not None and played.action is not None
    frm, to = look_board.named_move(played.say)
    assert _legal(MOVES_10_OCT, frm, to), played.say
    assert _squares(played.action) == (frm, to), "the arrow is the move said"
    assert played.action.exact and played.action.is_move
    assert "the position from the move list" in played.note
    assert "the look said f3→e4, not a legal move" in played.note, "the model's move, in the log"


def test_without_a_move_list_the_board_reading_is_played():
    played = look_chess.play(_look({"moves": [], "fen": FEN_10_OCT + " w KQkq - 0 4", "white_at_bottom": True}),
                             _picture())
    assert played is not None and "its reading of the board" in played.note
    frm, to = look_board.named_move(played.say)
    assert _legal(FEN_10_OCT + " w KQkq - 0 4", frm, to)


def test_black_at_the_bottom():
    moves = ["e4", "e5", "Nf3"]                                   # black to move, black plays from the bottom
    pos = look_chess._replay(moves)[0]
    placement = pos.fen().split()[0]
    played = look_chess.play(_look({"moves": moves, "fen": placement, "white_at_bottom": False}),
                             _picture(placement, white_bottom=False, lit=("g1", "f3")))
    assert played is not None and played.action is not None
    frm, to = look_board.named_move(played.say)
    assert _legal(moves, frm, to)
    assert _squares(played.action, white_bottom=False) == (frm, to)


def test_a_board_written_down_as_it_looks_is_turned_round():
    """Black at the bottom, and the FEN written top row first as it looks."""
    pos = look_chess._replay(["e4", "e5", "Nf3"])[0]
    placement = pos.fen().split()[0]
    as_it_looks = look_chess._placement(look_chess._cells(placement)[::-1])
    played = look_chess.play(_look({"moves": [], "fen": as_it_looks, "white_at_bottom": False}),
                             _picture(placement, white_bottom=False, lit=("g1", "f3")))
    assert played is not None and "turned round" in played.note
    frm, to = look_board.named_move(played.say)
    assert _legal(pos.fen(), frm, to)


# -- a reading the screen does not bear out is not played -------------------------------------------------------

def test_a_move_list_a_move_behind_is_not_played(caplog):
    with caplog.at_level(logging.INFO, logger="tora.chess"):
        played = look_chess.play(_look({"moves": MOVES_10_OCT[:-1], "fen": ""}), _picture())
    assert played is None
    assert "not the move the board shows" in caplog.text or "no reading" in caplog.text


def test_a_misread_board_is_not_played():
    misread = "rnbqkbnr/ppp2ppp/8/4p3/4p3/2N5/PPPP1PPP/R1BQKBNR"     # the f3 knight back on g1
    assert look_chess.play(_look({"moves": [], "fen": misread}), _picture()) is None


def test_no_board_on_screen_keeps_the_look():
    assert look_chess.play(_look({"moves": MOVES_10_OCT, "fen": FEN_10_OCT}), _picture(board=False)) is None


def test_a_look_without_a_reading_keeps_the_look():
    assert look_chess.play(_look({}), _picture()) is None


def test_on_the_opponents_turn_it_says_so_and_draws_nothing():
    moves = MOVES_10_OCT + ["4. Nxe5"]                             # black to move, white at the bottom
    pos = look_chess._replay(look_chess.moves_of(moves))[0]
    played = look_chess.play(_look({"moves": moves, "fen": ""}), _picture(pos.fen().split()[0], lit=("f3", "e5")))
    assert played is not None and played.action is None
    assert played.say == "It's Black's move now, not yours - ask me again after it."


# -- through the look, as the user sees and hears it ------------------------------------------------------------

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


def test_the_user_hears_and_sees_the_engines_move(monkeypatch):
    mgr = _manager(_picture())

    async def fake_look(*_a, **_kw):
        return _look({"moves": MOVES_10_OCT, "fen": FEN_10_OCT, "white_at_bottom": True})
    monkeypatch.setattr(gemini_vision, "look", fake_look)
    monkeypatch.setattr(cm, "active_window_title", lambda: "Play Chess Online Against the Computer - Chess.com")
    out = asyncio.run(mgr._tool_look_at_screen({"question": "what's my best move?", "careful": True}))
    frm, to = look_board.named_move(out["say"])
    assert _legal(MOVES_10_OCT, frm, to) and "f3 to e4" not in out["say"]
    (tag,) = [d["tag"] for d in mgr._drawn]
    x0, y0, x1, y1 = [int(v) for v in tag[len("[ARROW:"):-len(":tora]")].replace("->", ",").split(",")]
    arrow = SimpleNamespace(center=(x0, y0), to_center=(x1, y1))
    assert _squares(arrow) == (frm, to)


# -- the look reads the game only when it is a move in a game ---------------------------------------------------

def test_a_game_look_asks_for_the_reading_and_others_do_not(monkeypatch):
    sent = []

    async def fake_generate_json(model, contents, schema, **kw):
        sent.append((schema, kw["system"]))
        return {"answer": "Knight from g1 to f3.", "actions": [], "sure": True,
                "chess": {"moves": ["e4", "e5"], "fen": "x", "white_at_bottom": True}}
    monkeypatch.setattr(gemini, "generate_json", fake_generate_json)
    look = asyncio.run(gemini_vision.look("what's my best move?", "img", title="Play Chess Online - Chess.com"))
    schema, system = sent[-1]
    assert schema["propertyOrdering"][0] == "chess" and "chess" in schema["properties"]
    assert "an engine plays" in system
    assert look.chess["moves"] == ["e4", "e5"]
    sent.clear()
    asyncio.run(gemini_vision.look("where do I sign in?", "img"))
    assert all("chess" not in schema["properties"] and "an engine plays" not in system for schema, system in sent)
