"""Show the next move, wave 1 (docs/SHOW_THE_NEXT_MOVE.md, SN-01..03).

A user on chess.com asked for their best move (5 Oct 2026). Tora read the
board back to them - "Black has 7 pawns... let me recount" - and ringed
"Knight on b1" and "c3 square" without ever drawing the move. A move could
not be an arrow: every highlight was a labelled circle. Now a what-to-do
question gets one arrow and one line, the look's readings stay in the log,
and every screen answer is timed.
"""
from __future__ import annotations

import asyncio
import logging
import os
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import gemini, gemini_vision


def _bare_manager():
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    drawn = []
    mgr.sig_draw = SimpleNamespace(emit=drawn.append)
    mgr.sig_clear_drawings = SimpleNamespace(emit=lambda: None)
    mgr._drawn = drawn
    mgr._privacy_guard = False
    mgr._activity = lambda what: None
    shot = SimpleNamespace(base64_jpeg="x")

    async def capture(**_kw):
        return [shot]
    mgr._capture_shots = capture
    mgr._active_shot = lambda screens: shot
    mgr._shape_from_tag = lambda tag: {"tag": tag}
    return mgr


def _the_chess_look():
    """What the deep look returned for the photo: two labelled circles - and
    now, from the new schema, the move itself."""
    return gemini_vision._parse({
        "answer": "Knight from b1 to c3. It develops a piece and guards e4. Black has 7 pawns.",
        "actions": [
            {"label": "Knight on b1", "box": [880, 100, 980, 200], "kind": "circle"},
            {"label": "c3 square", "box": [680, 300, 780, 400], "kind": "circle"},
            {"label": "Nc3", "box": [880, 100, 980, 200], "kind": "move", "to_box": [680, 300, 780, 400]},
        ],
        "observations": ["White: Ke1, Qd1, Nb1, Bc3 ...", "Black has 7 pawns (a5, c6, d7, e7, f7, g7, h7)"],
    }, "gemini-3.1-pro-preview", True)


def _ask(mgr, monkeypatch, look, question):
    async def fake_look(*_a, **_kw):
        return look
    monkeypatch.setattr(gemini_vision, "look", fake_look)
    monkeypatch.setattr(cm, "active_window_title", lambda: "Play Chess Online - Chess.com")
    return asyncio.run(mgr._tool_look_at_screen({"question": question, "careful": True}))


# -- SN-03: a move is an arrow --------------------------------------------------------------------------------

def test_a_move_is_one_arrow_from_the_piece_to_its_square():
    move = _the_chess_look().actions[2]
    assert move.is_move and move.as_tag() == "[ARROW:150,930->350,730:tora]"
    assert "Nc3" not in move.as_tag(), "no label on the board"


def test_a_move_without_its_square_is_not_an_arrow():
    look = gemini_vision._parse({"answer": "x", "actions": [
        {"label": "knight", "box": [880, 100, 980, 200], "kind": "move"}]}, "m", False)
    assert not look.actions[0].is_move and look.actions[0].as_tag().startswith("[CIRCLE")


@pytest.mark.parametrize("q", ["what's my best move?", "what is the best move here", "what should I do next",
                               "which one should I pick", "where should I click", "what's my next move",
                               "what can I play here?"])
def test_what_should_i_do_questions(q):
    assert gemini_vision.is_next_action(q)


@pytest.mark.parametrize("q", ["what's on my screen", "what does this chart show", "explain this error",
                               "how many pawns does black have", "what's wrong with this equation"])
def test_other_questions_are_not(q):
    assert not gemini_vision.is_next_action(q)


def test_the_best_move_draws_one_arrow_and_nothing_else(monkeypatch):
    mgr = _bare_manager()
    out = _ask(mgr, monkeypatch, _the_chess_look(), "what's my best move?")
    assert mgr._drawn == [{"tag": "[ARROW:150,930->350,730:tora]"}], "the move, and no circles"
    assert out["drawn"] == ["arrow: Nc3"]
    assert out["highlights"] == [{"label": "Nc3", "tag": "[ARROW:150,930->350,730:tora]"}]


def test_other_looks_still_ring_what_the_answer_rests_on(monkeypatch):
    mgr = _bare_manager()
    _ask(mgr, monkeypatch, _the_chess_look(), "explain this position")
    assert len(mgr._drawn) == 3


# -- SN-02: one line, never thinking out loud --------------------------------------------------------------------

def test_the_voice_gets_a_line_to_say_and_never_the_readings(monkeypatch, caplog):
    mgr = _bare_manager()
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        out = _ask(mgr, monkeypatch, _the_chess_look(), "what's my best move?")
    assert out["say"] == "Knight from b1 to c3. It develops a piece and guards e4.", "the move, and why"
    assert "observations" not in out and "7 pawns" not in repr(out), "the readings never reach the voice"
    assert "7 pawns" in caplog.text, "they stay in tora.log, for diagnosis"


@pytest.mark.parametrize("raw, said", [
    ("Knight to c3. It develops a piece and guards e4. Black has 7 pawns.",
     "Knight to c3. It develops a piece and guards e4."),
    ("• Black has 7 pawns\n* Let me recount", "Black has 7 pawns Let me recount"),
    ("Play **Nf3**, developing the knight toward the centre.", "Play Nf3, developing the knight toward the centre."),
])
def test_one_line(raw, said):
    assert cm._one_line(raw) == said


def test_the_rules_say_say_it_and_stop():
    import config
    from ai import gemini_realtime
    rules = " ".join(config._REALTIME_RULES.split())
    assert "Never read out what it saw" in rules
    assert '"WHAT SHOULD I DO?"' in rules and "one arrow from the piece to its square" in rules
    look = next(d for d in gemini_realtime.tool_declarations()[0]["functionDeclarations"]
                if d["name"] == "look_at_screen")
    assert "returns `say`" in look["description"] and "describe the highlight" not in look["description"]
    assert "ONE thing in actions" in gemini_vision._SYSTEM
    assert "never read to the user" in gemini_vision._OBSERVATIONS_RULE, "the deep look's trail (SP-05)"


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])     # kept alive by the fixture


def test_the_card_shows_words_not_bullets(qapp):
    from ui.flow_bar import FlowBar
    bar = FlowBar()
    try:
        bar.push_answer("• Black has **7** pawns")
        assert bar._text == " Black has 7 pawns"
    finally:
        bar.close()


# -- SN-01: every screen answer measured ---------------------------------------------------------------------------

def test_an_answer_that_looked_leaves_a_timeline_and_a_scorecard(caplog):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._ans = {"heard": 100.0, "asked": 101.0}
    import time as _t
    stamps = iter([102.0, 109.5, 109.6, 110.4])
    real = _t.monotonic
    try:
        cm.time.monotonic = lambda: next(stamps)
        mgr._mark_answer("tool")
        mgr._mark_answer("look", "deep gemini-3.1-pro-preview, 1290 in / 85 out / 1402 thinking")
        mgr._mark_answer("drawing", "arrow")
        mgr._mark_answer("audio")
    finally:
        cm.time.monotonic = real
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr._log_answer("Knight from b1 to c3.")
    assert ("answer timeline: look called +1.0s, look +8.5s (deep gemini-3.1-pro-preview, 1290 in / 85 out / "
            "1402 thinking), drawn +8.6s (arrow), voice +9.4s, said 21 chars") in caplog.text
    assert "answer scorecard: 1 look(s), drawn p50 8.6s, 1 arrow(s), 0 with nothing drawn, voice p50 9.4s" in caplog.text
    assert mgr._ans is None


def test_an_answer_with_no_look_logs_nothing(caplog):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._ans = {"heard": 1.0}
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr._log_answer("Hello.")
    assert "answer timeline" not in caplog.text


def test_a_look_reports_its_tokens(monkeypatch):
    async def fake_generate(*_a, **_kw):
        return {"candidates": [{"content": {"parts": [{"text": '{"answer": "Nc3.", "actions": []}'}]}}],
                "usageMetadata": {"promptTokenCount": 1290, "candidatesTokenCount": 85, "thoughtsTokenCount": 1402}}
    monkeypatch.setattr(gemini, "generate", fake_generate)
    look = asyncio.run(gemini_vision.look("what's my best move?", "x", deep=True))
    assert look.answer == "Nc3." and look.usage["thoughtsTokenCount"] == 1402
