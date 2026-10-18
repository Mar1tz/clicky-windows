"""The flow bar (4 Oct 2026): Tora at the edge of the screen, after Wispr Flow.

"While I'm talking I want this to look like Wispr Flow's bottom UI" - a
small black pill that shows her listening (bars), thinking (a turning
circle) and speaking, with a card for what she says, that can be dragged to
any side of the screen and changes to fit it. Shown live first; "it all
looks amazing, please do this." The top island stays, but her words no
longer go there.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bar(qapp):
    from ui.flow_bar import FlowBar
    b = FlowBar()
    yield b
    b.close()


def _settle(b):
    """As the bar looks once its grow has finished."""
    b._retarget()
    b._size = b._size_to
    b._buttons_a = 1.0 if (b._state == "listening" and b._hands_free) else 0.0


def _work_area():
    from PyQt6.QtGui import QGuiApplication
    return QGuiApplication.primaryScreen().availableGeometry()


# -- where it sits ------------------------------------------------------------------------------------------------

def test_a_dock_reads_back_and_anything_else_is_the_bottom():
    from ui.flow_bar import parse_dock
    assert parse_dock("right|0.250|1920,0") == ("right", 0.25, "1920,0")
    for junk in ("", "middle|0.5|", "left|lots|", None):
        assert parse_dock(junk)[:2] == ("bottom", 0.5), junk
    assert parse_dock("top|7|")[1] == 1.0, "kept on the screen"


def test_the_nearest_side_is_found():
    from PyQt6.QtCore import QPoint, QRect
    from ui.flow_bar import nearest_edge
    geo = QRect(0, 0, 1920, 1040)
    assert nearest_edge(geo, QPoint(960, 1030)) == ("bottom", 0.5)
    assert nearest_edge(geo, QPoint(5, 260))[0] == "left"
    assert nearest_edge(geo, QPoint(1910, 520)) == ("right", 0.5)
    assert nearest_edge(geo, QPoint(480, 3)) == ("top", 0.25)


@pytest.mark.parametrize("edge", ["bottom", "top", "left", "right"])
def test_it_sits_on_every_side_and_its_card_opens_inwards(bar, edge):
    bar.set_dock(f"{edge}|0.5|")
    bar.set_state("speaking")
    _settle(bar)
    bar._place()
    geo = _work_area()
    pill = bar.pill.pill_rect().translated(bar.pill.pos().toPointF())
    card = bar.card.card_rect().translated(bar.card.pos().toPointF())
    gap = {"bottom": geo.bottom() + 1 - pill.bottom(), "top": pill.top() - geo.top(),
           "left": pill.left() - geo.left(), "right": geo.right() + 1 - pill.right()}[edge]
    assert abs(gap - 12) <= 1, f"{edge}: {gap}px from the edge"
    assert bar.upright() == (edge in ("left", "right"))
    assert (pill.height() > pill.width()) == bar.upright(), "stands up on the sides"
    inwards = {"bottom": card.bottom() <= pill.top(), "top": card.top() >= pill.bottom(),
               "left": card.left() >= pill.right(), "right": card.right() <= pill.left()}[edge]
    assert inwards, f"{edge}: the card opens towards the middle"
    assert geo.contains(card.toRect()), "and stays on the screen"


def test_dragging_it_to_a_side_keeps_it_there(bar):
    from PyQt6.QtCore import QPoint
    docks = []
    bar.dock_changed.connect(docks.append)
    geo = _work_area()
    bar._begin_drag()
    bar._drag_to(QPoint(geo.right() - 4, geo.center().y()))
    assert bar.upright(), "it turns as it nears a side"
    bar._end_drag(QPoint(geo.right() - 4, geo.center().y()))
    assert docks and docks[-1].startswith("right|") and bar.dock() == docks[-1]
    end = bar._snap[1]
    assert end == bar._home(), "and settles against that side"


# -- what it shows -------------------------------------------------------------------------------------------------

def test_each_state_has_one_size(bar):
    from ui.flow_bar import SIZES
    for state, hands, look in (("idle", False, "idle"), ("listening", False, "listening"),
                               ("listening", True, "hands_free"), ("thinking", False, "thinking"),
                               ("speaking", False, "speaking")):
        bar.set_state(state)
        bar.set_hands_free(hands)
        _settle(bar)
        assert bar._size == SIZES[look], state
    bar.set_state("idle")
    bar._hover = True
    _settle(bar)
    assert bar._size == SIZES["hover"], "bigger under the mouse"


def test_app_state_names_work_and_unknown_is_idle(bar):
    bar.set_state("LISTENING")
    assert bar._state == "listening"
    bar.set_state("ROBOT")
    assert bar._state == "idle"


def test_the_card_keeps_time_with_her_voice_and_never_grows(bar):
    bar.set_state("speaking")
    text = "x" * 100
    bar.push_answer(text)
    bar.set_voice_clock(lambda: (1.0, 4.0))          # a quarter of what has come is heard
    bar._tick_card(time.monotonic(), 0.016)
    assert 25 <= bar._shown <= 32, bar._shown
    size = bar.card.size()
    bar.push_answer(" and then a great deal more" * 40)
    bar._tick_card(time.monotonic(), 0.016)
    assert bar.card.size() == size, "the card keeps its size; its text rolls"
    bar.set_voice_clock(None)
    bar.finish_answer()
    for _ in range(400):
        bar._tick_card(time.monotonic(), 0.05)
    assert bar._shown == len(bar._text), "finished: all of it"


def test_a_new_question_clears_the_last_answer(bar):
    bar.set_state("speaking")
    bar.push_answer("The export button is under File.")
    bar.finish_answer()
    assert bar._card_want
    bar.set_state("idle")
    bar.set_state("listening")
    assert not bar._card_want and bar._text == ""


def test_the_card_goes_by_itself_a_while_after_she_has_finished(bar):
    from ui.flow_bar import CARD_HOLD_S
    bar.set_state("speaking")
    bar.push_answer("Done.")
    bar.finish_answer()
    bar.set_state("idle")
    bar._finished_at = time.monotonic() - CARD_HOLD_S - 1
    bar._tick_card(time.monotonic(), 0.016)
    assert not bar._card_want
    bar._card_want, bar._card_hover = True, True
    bar._tick_card(time.monotonic(), 0.016)
    assert bar._card_want, "not while the mouse is on it"


def test_copy_puts_the_whole_answer_on_the_clipboard(bar, qapp):
    from PyQt6.QtGui import QGuiApplication
    bar.push_answer("The export button ")
    bar.push_answer("is under File.")
    bar.copy_answer()
    assert QGuiApplication.clipboard().text() == "The export button is under File."


def test_the_buttons(bar):
    asked = []
    bar.cancel_requested.connect(lambda: asked.append("cancel"))
    bar.done_requested.connect(lambda: asked.append("done"))
    bar.talk_requested.connect(lambda: asked.append("talk"))
    bar.set_state("idle")
    _settle(bar)
    bar._click(bar.pill.pill_rect().center())
    assert asked == ["talk"], "a click on the resting pill: talk"
    bar.set_state("listening")
    _settle(bar)
    assert bar.pill._buttons() == (None, None), "holding the shortcut: no buttons"
    bar._click(bar.pill.pill_rect().center())
    assert asked == ["talk"]
    bar.set_hands_free(True)
    _settle(bar)
    cancel, done = bar.pill._buttons()
    bar._click(cancel.center())
    bar._click(done.center())
    assert asked == ["talk", "cancel", "done"]


def test_it_never_takes_the_keyboard(bar):
    from PyQt6.QtCore import Qt
    for w in (bar.pill, bar.card):
        assert w.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert w.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_it_paints_every_look(bar):
    from PyQt6.QtGui import QImage, QPainter
    img = QImage(400, 300, QImage.Format.Format_ARGB32)
    for edge in ("bottom", "right"):
        bar.set_dock(f"{edge}|0.5|")
        for state, hands in (("idle", False), ("listening", False), ("listening", True),
                             ("thinking", False), ("speaking", False)):
            bar.set_state(state)
            bar.set_hands_free(hands)
            bar.set_level(0.05)
            _settle(bar)
            p = QPainter(img)
            bar._paint_pill(p, bar.pill.pill_rect())
            p.end()
    bar.push_answer("Here it is.")
    bar._card_a = 1.0
    p = QPainter(img)
    bar._paint_card(p, bar.card)
    p.end()


# -- the companion's side -----------------------------------------------------------------------------------------

def _manager(state):
    from companion_manager import AppState, CompanionManager
    m = CompanionManager.__new__(CompanionManager)
    m._state = getattr(AppState, state)
    m._end_claimed = False
    import threading
    m._end_lock, m._begin_lock = threading.Lock(), threading.Lock()
    m._rt_streaming = True
    m._heard_buf = ["half a"]
    m._realtime = SimpleNamespace()
    m.done = []
    m._listener = SimpleNamespace(stop_recording=lambda: m.done.append("mic off") or b"")
    m._emit_state = lambda s: (setattr(m, "_state", s), m.done.append(s.name))
    m._submit = lambda coro: (m.done.append(coro.__name__), coro.close())
    m.sig_hands_free = SimpleNamespace(emit=lambda on: m.done.append(("hands free", on)))
    return m


def test_cancel_forgets_what_was_said_and_starts_a_fresh_voice():
    m = _manager("LISTENING")
    m.cancel_listening()
    assert m.done == ["mic off", "IDLE", "_restart_realtime"] and m._heard_buf == []
    m.cancel_listening()
    assert m.done.count("IDLE") == 1, "once"


def test_done_is_the_hotkey_coming_up():
    m = _manager("LISTENING")
    m.finish_listening()
    assert m.done == ["_end_capture_and_process"]
    idle = _manager("IDLE")
    idle.finish_listening()
    assert idle.done == []


def test_a_click_on_the_pill_listens_hands_free():
    m = _manager("IDLE")
    m._begin_capture = lambda: m.done.append("listening")
    m.start_hands_free()
    assert m.done == ["listening", ("hands free", True), "_auto_stop_on_silence"]


def test_a_tap_of_the_shortcut_is_hands_free(monkeypatch):
    from config import cfg
    m = _manager("LISTENING")
    m._hotkey_pressed_at = time.monotonic()
    monkeypatch.setattr(cfg, "tap_max_seconds", 0.6, raising=False)
    m.on_hotkey_release()
    assert ("hands free", True) in m.done


def test_where_it_was_left_is_kept(monkeypatch):
    from config import cfg
    written = {}
    monkeypatch.setattr(cfg, "_write_env", lambda k, v: written.__setitem__(k, v))
    monkeypatch.delenv("TORA_FLOW_DOCK", raising=False)
    assert cfg.flow_dock() == ""
    cfg.set_flow_dock("left|0.300|0,0")
    assert cfg.flow_dock() == "left|0.300|0,0" and written == {"TORA_FLOW_DOCK": "left|0.300|0,0"}


def test_main_wires_it_and_her_words_go_to_the_card_not_the_island():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    for wire in ("manager.sig_response_chunk.connect(flow.push_answer)",
                 "manager.sig_response_done.connect(flow.finish_answer)",
                 "flow.set_voice_clock(manager.voice_clock)",
                 "manager.sig_audio_level.connect(flow.set_level)",
                 "flow.set_state(state.name)",
                 "manager.sig_hands_free.connect(flow.set_hands_free)",
                 "flow.cancel_requested.connect(manager.cancel_listening)",
                 "flow.done_requested.connect(manager.finish_listening)",
                 "flow.talk_requested.connect(manager.start_hands_free)",
                 "flow.dock_changed.connect(cfg.set_flow_dock)",
                 "flow.show()"):
        assert wire in src, wire
    assert "overlay.caption_chunk" not in src and "overlay.caption_done" not in src


# -- at rest, the bar stops ticking (UI-02, the speed review of 17 Oct 2026) ---------------------------------------

def test_the_bar_stops_ticking_at_rest_and_wakes_on_a_change(bar):
    import time as _time
    bar.show()
    assert bar._timer.isActive()
    bar._size_t0 = _time.monotonic() - 1.0          # its grow long finished
    _settle(bar)
    bar._tick()
    assert not bar._timer.isActive(), "idle and still: no 60-a-second wake-ups"
    bar.set_state("listening")
    assert bar._timer.isActive(), "a press wakes it at once"


def test_the_card_is_repainted_only_when_it_changes(bar, monkeypatch):
    bar.show()
    bar.push_answer("Knight to f3.")
    bar.finish_answer()
    painted = []
    monkeypatch.setattr(bar.card, "update", lambda: painted.append(1))
    bar._tick_card(1.0, 0.016)
    bar._card_a = 1.0
    bar._shown = float(len(bar._text))
    bar._tick_card(1.0, 0.016)
    before = len(painted)
    for _ in range(5):
        bar._tick_card(1.0, 0.016)                  # nothing changed on it
    assert len(painted) == before
