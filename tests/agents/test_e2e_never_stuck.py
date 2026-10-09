"""FS-01 (docs/FAST_AND_RIGHT.md): "I've done that" means a new screen.

9 Oct 2026, on chess.com: "What's my next best move?" - "Pawn d2 to d4".
They moved. "Okay, I've done that. Where's my next best move?" - "Pawn d2 to
d4" again: the look before was reused, its 16x9 sketch blind to a moved
piece. Then "Okay, look again" and "I've done that. Please do the next move."
got the stale "Knight f3 to d4" twice more. "It did it, and when I asked the
next thing it was still stuck and didn't realise."
"""
from __future__ import annotations

import asyncio
import base64
import io
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import gemini_vision, look_region


def _board(pawn_square: str) -> str:
    """A 1024x576 chess.com-like board, one white pawn on `pawn_square`."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1024, 576), (49, 46, 43))
    d = ImageDraw.Draw(im)
    side, left, top = 64, 256, 32
    for r in range(8):
        for c in range(8):
            colour = (238, 238, 210) if (r + c) % 2 == 0 else (118, 150, 86)
            d.rectangle((left + c * side, top + r * side, left + (c + 1) * side - 1, top + (r + 1) * side - 1),
                        fill=colour)
    col, row = "abcdefgh".index(pawn_square[0]), 8 - int(pawn_square[1])
    cx, cy = left + col * side + side // 2, top + row * side + side // 2
    d.ellipse((cx - 18, cy - 22, cx + 18, cy + 22), fill=(250, 250, 250), outline=(20, 20, 20), width=3)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=75)
    return base64.b64encode(buf.getvalue()).decode()


def test_a_moved_piece_is_a_changed_screen():
    before, after = look_region.sketch(_board("d2")), look_region.sketch(_board("d4"))
    assert look_region.changed_cells(before, look_region.sketch(_board("d2"))) <= cm.CompanionManager.SAME_CELLS
    assert look_region.changed_cells(before, after) > cm.CompanionManager.SAME_CELLS, \
        "the old 16x9 sketch called these the same screen"


def _manager(monkeypatch, images, title="Notes"):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    events = []
    mgr.sig_draw = SimpleNamespace(emit=lambda sh: events.append("draw"))
    mgr.sig_clear_drawings = SimpleNamespace(emit=lambda: events.append("clear"))
    mgr._privacy_guard = False
    mgr._activity = lambda what: None
    mgr._heard_buf = []
    mgr._last_look = None
    mgr.CLEAR_BEFORE_SHOT_S = 0.0
    queue = list(images)

    async def capture(**_kw):
        events.append("capture")
        return [SimpleNamespace(base64_jpeg=queue.pop(0) if len(queue) > 1 else queue[0])]
    mgr._capture_shots = capture
    mgr._active_shot = lambda screens=None: screens[0] if screens else None
    mgr._shape_from_tag = lambda tag: {"tag": tag}
    monkeypatch.setattr(cm, "active_window_title", lambda: title)
    calls = []

    async def fake_look(question, image, **kw):
        calls.append(question)
        return gemini_vision.Look(answer="Pawn from d2 to d4.", sure=True, path="deep", model="pro", cost=0.006,
                                  actions=[gemini_vision.Action("pawn", (700, 300, 760, 360), "move",
                                                                to_box=(500, 300, 560, 360))])
    monkeypatch.setattr(gemini_vision, "look", fake_look)
    return mgr, calls, events


def _ask(mgr, heard, question):
    mgr._heard_buf = [heard]
    return asyncio.run(mgr._tool_look_at_screen({"question": question}))


@pytest.mark.parametrize("heard", [
    "Okay, I've done that. Where's my next best move?",
    "Okay, look again's next move.",
    "I've done that. Please do the next move.",
    "what about now?",
    "I clicked it, what do I do?",
])
def test_the_9_october_sentences_each_look_again(monkeypatch, heard):
    """Even on a screen the sketch calls the same: their words say it changed."""
    mgr, calls, _ = _manager(monkeypatch, [_board("d2")])
    _ask(mgr, "how do I pay?", "how do I pay?")
    _ask(mgr, heard, "how do I pay?")
    assert len(calls) == 2


def test_a_move_in_a_game_is_never_reused(monkeypatch):
    mgr, calls, _ = _manager(monkeypatch, [_board("d2")], title="Play Chess Online - Chess.com - Opera")
    _ask(mgr, "what's my best move", "what's my best move")
    _ask(mgr, "what's my best move", "what's my best move")
    assert len(calls) == 2, "the other side moves too"


def test_the_moved_board_is_looked_at_again_even_said_the_same_way(monkeypatch):
    mgr, calls, _ = _manager(monkeypatch, [_board("d2"), _board("d4")])
    _ask(mgr, "how do I pay?", "how do I pay?")
    _ask(mgr, "how do I pay?", "how do I pay?")
    assert len(calls) == 2


def test_show_me_that_again_still_shows_it_again(monkeypatch):
    mgr, calls, _ = _manager(monkeypatch, [_board("d2")])
    _ask(mgr, "how do I pay?", "how do I pay?")
    out = _ask(mgr, "show me that again", "where should I click to pay")
    assert len(calls) == 1 and out.get("reused") is True


def test_old_drawings_are_rubbed_out_before_the_screenshot(monkeypatch):
    mgr, _calls, events = _manager(monkeypatch, [_board("d2")])
    _ask(mgr, "how do I pay?", "how do I pay?")
    assert events.index("clear") < events.index("capture"), "her last arrow is not in her next look"
