"""The new first launch, phase B (29 Oct 2026): hello, can I hear you, this
is how you talk to me - each proven by doing it (docs/ui/HEYCLICKY_ONBOARDING.md).
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class FakeVoice:
    """The tour's conversation, without Google: records what the steps ask
    of it; a test plays her side by calling `say`."""

    def __init__(self, emit):
        self.say = emit
        self.calls = []

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def begin_interview(self):
        self.calls.append("interview")

    def hold(self, down, look=None):
        self.calls.append(("hold", down) if look is None else ("hold", down, look))

    def type(self, text):
        self.calls.append(("type", text))

    def skip(self):
        self.calls.append("skip")
        self.say("skip", None)

    def one_more_thing(self):
        self.calls.append("one more thing")


def throw_away(panel) -> None:
    """A panel gone for good before the next test: its step left, then
    deleted now - not on some later test's processEvents, where what it
    still had queued once crashed the run (30 Oct)."""
    from PyQt6.QtCore import QCoreApplication, QEvent
    try:
        panel.step().leave()
    except Exception:  # noqa: BLE001 - a test may have left it half-way
        pass
    panel.hide()
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()


@pytest.fixture
def panel(qapp, monkeypatch):
    import ui.onboarding_parts as OP
    import ui.onboarding_shape as O
    import ui.onboarding_steps as St
    monkeypatch.setattr(O, "animations_on", lambda: False)
    played = []
    monkeypatch.setattr(OP, "play_voice", lambda name: played.append(name) or True)
    monkeypatch.setattr(St.MicStep, "microphones",
                        staticmethod(lambda: [(0, "Built-in", True), (3, "Headset", False)]))
    monkeypatch.setattr(St.MicStep, "_open", lambda self, device: None)
    monkeypatch.setattr(St, "Interview", FakeVoice)
    monkeypatch.setattr(St, "screen_picture", lambda trail=(): f"jpeg:{len(trail)}")
    monkeypatch.setattr(St.DriveStep, "MOVE_MOUSE", False)
    import hotkey
    monkeypatch.setattr(hotkey.DoubleTap, "start", lambda self: None)   # no real keyboard hook
    p = O.OnboardingPanel(St.steps())
    p.played = played
    yield p
    throw_away(p)


def _run_timers(qapp, ms=500):
    import time
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


# ── hello ──────────────────────────────────────────────────────────────────

def test_hello_plays_her_voice_once(panel, qapp):
    panel.open_from()
    _run_timers(qapp)
    assert len(panel.played) == 1, "the bundled hello - no talk message"
    assert panel.go.text() == "Show me" and panel.go.isEnabled()
    panel.next()
    panel.back()
    _run_timers(qapp)
    assert len(panel.played) == 1, "not again on the way back"


def test_hello_is_quiet_with_the_sound_off(panel, qapp):
    panel.sound.setChecked(False)
    panel.open_from()
    _run_timers(qapp)
    assert panel.played == []


# ── can I hear you ─────────────────────────────────────────────────────────

def test_it_takes_real_speech_not_a_click(panel):
    import ui.onboarding_steps as St
    mic = _to_step(panel, St.MicStep)
    mic.hear(0.2, 0.05)                          # a knock on the desk
    for _ in range(5):
        mic.hear(0.001, 0.05)                    # a quiet room
    assert not panel.go.isEnabled()
    for _ in range(12):
        mic.hear(0.04, 0.05)                     # someone talking
    assert panel.go.isEnabled()
    assert mic.bars.level > 0.2, "the bars moved with it"


def test_the_microphone_chip_names_it_and_a_choice_is_saved(panel, monkeypatch):
    from config import cfg
    saved = []
    monkeypatch.setattr(cfg, "set_mic_device", saved.append)
    monkeypatch.setattr(cfg, "mic_device", lambda: None)
    import ui.onboarding_steps as St
    mic = _to_step(panel, St.MicStep)
    assert "Built-in" in mic.chip.text(), "Windows' default when none was chosen"
    mic._pick(3, "Headset")
    assert saved == [3] and "Headset" in mic.chip.text()


def test_no_microphone_does_not_trap_them(panel, monkeypatch):
    import ui.onboarding_steps as St
    monkeypatch.setattr(St.MicStep, "microphones", staticmethod(lambda: []))
    _to_step(panel, St.MicStep)
    assert panel.go.isEnabled() and "No microphone found" in panel.step().note.text()


# ── this is how you talk to me ─────────────────────────────────────────────

def _to_talk(panel):
    import ui.onboarding_steps as St
    return _to_step(panel, St.TalkStep)


def test_a_real_hold_proves_it_and_a_brush_does_not(panel):
    talk = _to_talk(panel)
    talk.hold(True, 10.0)
    assert all(cap._lit for cap in talk.keys), "the keys light up while held"
    talk.hold(False, 10.1)
    assert not any(cap._lit for cap in talk.keys)
    assert not panel.go.isEnabled() and "longer" in talk.status.text()
    talk.hold(True, 11.0)
    assert talk.status.text() == "listening…"
    talk.hold(False, 12.2)
    assert panel.go.isEnabled()


def test_with_the_app_running_the_shortcut_is_borrowed_and_given_back(panel):
    claimed, released = [], []
    live = SimpleNamespace(claim_hotkey=lambda down, up: claimed.append((down, up)),
                           release_hotkey=lambda: released.append(1))
    panel.attach(live)
    talk = _to_talk(panel)
    assert len(claimed) == 1, "holding it lights the keys, it doesn't start a conversation"
    assert not talk._poll.isActive()
    down, up = claimed[0]
    down()
    talk._down_at = 0.0                           # held long enough, by the clock here
    up()
    talk.pressed.emit(False)
    panel.back()
    assert released, "given back when the step goes"


def test_the_keycaps_are_the_users_own_shortcut(monkeypatch):
    from config import cfg
    import ui.onboarding_steps as St
    monkeypatch.setattr(cfg, "hotkey", "ctrl+alt")
    assert St.hotkey_keys() == ["Ctrl", "Alt"]
    monkeypatch.setattr(cfg, "hotkey", "ctrl+win")
    assert St.hotkey_keys() == ["Ctrl", "Win"]


def test_it_takes_its_colours_from_the_tokens():
    from tests.agents.test_e2e_design_tokens import ROOT, _code_hexes
    assert not _code_hexes(ROOT / "ui/onboarding_steps.py")


# ── phase C: she hears your name ───────────────────────────────────────────

def test_holding_the_keys_is_heard_by_her_voice_and_she_learns_the_name(panel):
    talk = _to_talk(panel)
    voice = panel.voice
    assert "start" in voice.calls, "the voice opens as the step does"
    talk.hold(True, 10.0)
    talk.hold(False, 11.0)
    assert ("hold", True) in voice.calls and ("hold", False) in voice.calls
    voice.say("name", "Dylan")
    voice.say("tora_end", "Hi Dylan, lovely to meet you!")
    assert talk.name == "Dylan" and "Dylan" in talk.status.text()
    assert talk.reply.text() == "Hi Dylan, lovely to meet you!"
    assert panel.go.isEnabled()


# ── phase C: I'm curious about you ─────────────────────────────────────────

def _to_interview(panel):
    talk = _to_talk(panel)
    talk.hold(True, 10.0)
    talk.hold(False, 11.0)
    panel.next()
    return panel.step()


def test_the_interview_is_wide_and_she_starts_asking(panel):
    from ui.onboarding_shape import WIDE
    iv = _to_interview(panel)
    assert iv.TITLE.startswith("I'm curious") and panel.panel_rect().width() == WIDE[0]
    assert "interview" in panel.voice.calls
    assert not panel.go.isEnabled(), "four answers first"


def test_her_lines_and_yours_become_bubbles(panel):
    iv = _to_interview(panel)
    say = panel.voice.say
    say("state", "speaking")
    assert iv.status.text() == "Tora is speaking…"
    say("tora", "What do you ")
    say("tora", "mostly work on?")
    say("tora_end", "What do you mostly work on?")
    say("you", "I run a ")
    say("you_end", "I run a bakery.")
    say("state", "your_turn")
    assert iv.lines() == [(True, "What do you mostly work on?"), (False, "I run a bakery.")]
    assert iv.status.text().startswith("Your turn - hold Ctrl")


def test_typing_an_answer_sends_it(panel):
    iv = _to_interview(panel)
    iv.box.setText("  I write code  ")
    assert not iv.box.send.isHidden(), "a send arrow, once something is typed"
    iv.box.send_now()
    assert ("type", "I write code") in panel.voice.calls
    assert iv.lines() == [(False, "I write code")] and iv.box.text() == ""


def test_the_keys_answer_too(panel):
    iv = _to_interview(panel)
    iv.hold(True)
    assert iv.pill.state == "listening"
    iv.hold(False)
    assert panel.voice.calls[-2:] == [("hold", True), ("hold", False)]


def test_four_answers_are_kept_and_continue_lights(panel, monkeypatch):
    import ui.onboarding_steps as St
    from ui.onboarding_interview import Answers
    saved = []
    monkeypatch.setattr(St, "save", saved.append)
    iv = _to_interview(panel)
    got = Answers(name="Dylan", uses=["Developer", "Business owner"], facts=["Runs HeyTora"])
    panel.voice.say("done", got)
    assert saved == [got] and panel.go.isEnabled()
    assert "Dylan" in iv.status.text()


def test_no_voice_does_not_trap_them(panel):
    iv = _to_interview(panel)
    panel.voice.say("state", "offline")
    assert panel.go.isEnabled()
    assert iv.lines() and "couldn't connect" in iv.lines()[-1][1]


def test_the_tour_ends_her_voice(panel):
    _to_talk(panel)
    panel.skip()
    assert panel.voice.calls[-1] == "stop"


# ── phase D ────────────────────────────────────────────────────────────────

def _to_step(panel, cls):
    """Straight to a step: each one before it marked proven."""
    panel.open_from()
    while not isinstance(panel.step(), cls):
        panel.step().done()
        panel.next()
    return panel.step()


def test_asking_about_the_screen_keeps_the_panel_and_hands_her_the_screen(panel):
    from PyQt6.QtCore import QPointF
    import ui.onboarding_steps as St
    circle = _to_step(panel, St.CircleStep)
    panel.show()
    circle.hold(True, 10.0)
    assert panel.isVisible(), "it stays put (29 Oct) - left out of her picture instead"
    circle._follow.stop()
    for x, y in ((100, 100), (180, 100), (180, 160)):
        circle.trail.add(QPointF(x, y))
    circle.hold(False, 12.0)
    assert panel.voice.calls[-1] == ("hold", False, ("jpeg:3", St.CircleStep.NOTE))
    assert not panel.go.isEnabled(), "proven by her answer"
    panel.voice.say("tora_end", "That's your revenue for the quarter.")
    assert panel.go.isEnabled() and "revenue" in circle.reply.text()


def test_the_help_step_is_gone():
    import ui.onboarding_steps as St
    assert not hasattr(St, "HelpStep"), "30 Oct: it was the talk step again"


def test_the_circle_stays_until_she_answers(panel):
    from PyQt6.QtCore import QPointF
    import ui.onboarding_steps as St
    circle = _to_step(panel, St.CircleStep)
    circle.hold(True, 10.0)
    circle._follow.stop()
    for x, y in ((100, 100), (180, 100), (180, 160), (100, 160)):
        circle.trail.add(QPointF(x, y))
    circle.hold(False, 12.0)
    assert circle.trail.isVisible(), "the line they drew doesn't vanish as they let go"
    panel.voice.say("tora_end", "That's your revenue for the quarter.")
    circle._clear_line()
    assert not circle.trail.isVisible()


def test_circling_draws_a_line_and_she_gets_it_on_the_picture(panel):
    from PyQt6.QtCore import QPointF
    import ui.onboarding_steps as St
    circle = _to_step(panel, St.CircleStep)
    circle.hold(True, 10.0)
    circle._follow.stop()                          # the test draws, not the real mouse
    for x, y in ((100, 100), (180, 100), (180, 160), (100, 160), (100, 100)):
        circle.trail.add(QPointF(x, y))
    circle.hold(False, 12.0)
    assert panel.voice.calls[-1] == ("hold", False, ("jpeg:5", St.CircleStep.NOTE))


def test_no_circle_is_not_the_step_done(panel):
    import ui.onboarding_steps as St
    circle = _to_step(panel, St.CircleStep)
    circle.hold(True, 10.0)
    circle._follow.stop()
    circle.hold(False, 12.0)
    assert panel.voice.calls[-1] == ("hold", False) and "Draw round" in circle.status.text()


def _drive_all(drive):
    for _ in range(200):
        if not drive.driving:
            break
        drive.tick()


def test_she_makes_the_list_she_chose(panel):
    import ui.onboarding_steps as St
    drive = _to_step(panel, St.DriveStep)
    panel.voice.say("list", ["Call the bank", "Send the invoice", "Water plants"])
    assert drive.driving and "Esc" in drive.status.text()
    _drive_all(drive)
    assert drive.list.items() == [("Call the bank", True), ("Send the invoice", True),
                                  ("Water plants", False)]
    assert panel.go.isEnabled()


def test_the_link_asks_her_without_talking_and_offline_she_still_drives(panel):
    import ui.onboarding_steps as St
    drive = _to_step(panel, St.DriveStep)
    drive.instead.click()
    assert panel.voice.calls[-1] == ("type", St.DriveStep.ASK)
    panel.voice.say("state", "offline")
    _drive_all(drive)
    assert [t for t, _ in drive.list.items()] == St.DriveStep.PLAIN


def test_moving_the_mouse_takes_it_back(panel, monkeypatch):
    import ui.onboarding_steps as St
    drive = _to_step(panel, St.DriveStep)
    drive.drive(["One", "Two"])
    monkeypatch.setattr(drive, "_theirs", lambda: True)
    drive.tick()
    assert not drive.driving and "yours again" in drive.status.text() and panel.go.isEnabled()


def test_ctrl_ctrl_opens_the_box_and_she_answers(panel):
    import ui.onboarding_steps as St
    claimed = []
    from types import SimpleNamespace
    panel.attach(SimpleNamespace(claim_type_tap=lambda: claimed.append("claim"),
                                 release_type_tap=lambda: claimed.append("release")))
    typed = _to_step(panel, St.TypeStep)
    assert claimed == ["claim"], "the app's Ctrl Ctrl doesn't open the shape too"
    assert typed.box.isHidden()
    typed.tapped.emit()
    assert not typed.box.isHidden()
    typed.box.setText("hi there")
    typed.box.send_now()
    assert panel.voice.calls[-1] == ("type", "hi there") and not panel.go.isEnabled()
    panel.voice.say("tora_end", "Hi! Got your message.")
    assert panel.go.isEnabled() and typed.reply.text() == "Hi! Got your message."
    panel.back()
    assert claimed[-1] == "release"


# ── skipping the questions (29 Oct: "can we please skip" twice, and she asked on) ──

def test_the_skip_link_stops_her_and_moves_on(panel, qapp):
    import ui.onboarding_steps as St
    iv = _to_interview(panel)
    iv.MOVE_ON_MS = 0
    iv.skip_link.click()
    assert "skip" in panel.voice.calls, "she is told too, so she stops asking"
    assert "move on" in iv.status.text()
    _run_timers(qapp, 100)
    assert isinstance(panel.step(), St.CircleStep), "on to the next step by itself"


def test_asking_her_to_skip_keeps_what_she_learned(panel, qapp, monkeypatch):
    import ui.onboarding_steps as St
    from ui.onboarding_interview import Answers
    saved = []
    monkeypatch.setattr(St, "save", saved.append)
    iv = _to_interview(panel)
    iv.MOVE_ON_MS = 10_000
    got = Answers(name="Dylan", uses=["Developer"])
    panel.voice.say("skip", got)
    assert saved == [got] and panel.go.isEnabled()
    panel.voice.say("skip", got)
    assert saved == [got], "once"


def test_asking_again_after_taking_it_back_drives_again(panel, qapp, monkeypatch):
    import ui.onboarding_steps as St
    drive = _to_step(panel, St.DriveStep)
    drive.AFTER_WORDS_MS = 0
    panel.voice.say("list", ["One", "Two", "Three"])
    monkeypatch.setattr(drive, "_theirs", lambda: True)
    drive.tick()                                    # they moved the mouse
    assert not drive.driving and "Ask me again" in drive.status.text()
    monkeypatch.setattr(drive, "_theirs", lambda: False)
    drive.hold(True)
    drive.hold(False)                               # "do it again"
    panel.voice.say("tora_end", "Sure, here we go!")   # ... and she only talked
    _run_timers(qapp, 100)
    _drive_all(drive)
    assert [t for t, _ in drive.list.items()] == ["One", "Two", "Three"], "a fresh list, not six"


def test_her_list_wins_over_driving_anyway(panel, qapp):
    import ui.onboarding_steps as St
    drive = _to_step(panel, St.DriveStep)
    drive.instead.click()
    panel.voice.say("list", ["Pay rent"])
    panel.voice.say("tora_end", "Watch - I'll take the mouse.")
    _run_timers(qapp, 50)
    _drive_all(drive)
    assert drive.list.items() == [("Pay rent", True)]



# ── "press Continue for me", and pick my voice (29 Oct) ────────────────────────

def test_she_can_press_continue_for_them(panel):
    import ui.onboarding_steps as St
    drive = _to_step(panel, St.DriveStep)
    assert panel.voice.can_continue() is False, "not done: she says what is left"
    panel.voice.say("press", "continue")
    assert panel.step() is drive
    drive.done()
    assert panel.voice.can_continue() is True
    panel.voice.say("press", "continue")
    assert isinstance(panel.step(), St.TypeStep)


def test_pick_my_voice_plays_it_and_keeps_it(panel, monkeypatch):
    import ui.onboarding_steps as St
    from config import cfg
    kept = []
    monkeypatch.setattr(cfg, "set_voice", kept.append)
    step = _to_step(panel, St.MakeMeYoursStep)
    assert panel.go.isEnabled(), "she already has a voice"
    assert len(step.tiles) >= 12 and step.WIDE
    step.pick("Puck")
    assert kept == ["Puck"] and panel.played[-1] == "Puck"
    assert [t.name for t in step.tiles if t.isChecked()] == ["Puck"]


def test_with_the_app_running_the_voice_goes_through_it(panel, monkeypatch):
    from types import SimpleNamespace
    import ui.onboarding_steps as St
    from config import cfg
    monkeypatch.setattr(cfg, "set_voice", lambda n: (_ for _ in ()).throw(AssertionError("app's job")))
    picked = []
    panel.attach(SimpleNamespace(set_voice=picked.append))
    step = _to_step(panel, St.MakeMeYoursStep)
    step.pick("Kore")
    assert picked == ["Kore"]


def test_her_colour_is_picked_with_her_voice(panel, monkeypatch):
    from types import SimpleNamespace
    import ui.onboarding_steps as St
    painted = []
    panel.attach(SimpleNamespace(bridge=SimpleNamespace(set_cursor_colour=painted.append)))
    step = _to_step(panel, St.MakeMeYoursStep)
    assert St.steps()[1] is St.MakeMeYoursStep, "second, as HeyClicky asks it (30 Oct)"
    step.paint("teal")
    assert painted == ["teal"] and [t.key for t in step.colours if t.isChecked()] == ["teal"]


def test_each_step_is_only_as_tall_as_what_is_on_it(panel):
    import ui.onboarding_steps as St
    from ui.onboarding_shape import MAX_H, MIN_H
    hello = _to_step(panel, St.HelloStep)
    short = panel.panel_rect().height()
    talk = _to_step(panel, St.TalkStep)
    assert MIN_H <= short < panel.panel_rect().height() <= MAX_H, "30 Oct: so much white space"
    assert hello is not talk


def test_the_type_box_grows_the_panel_when_it_opens(panel, qapp):
    import ui.onboarding_steps as St
    typed = _to_step(panel, St.TypeStep)
    before = panel.panel_rect().height()
    typed.tapped.emit()
    _run_timers(qapp, 60)
    assert panel.panel_rect().height() > before


# ── the owner's second walk-through (30 Oct) ───────────────────────────────

def _click(widget, hold_s=0.0):
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    import time as _t
    at = QPointF(10, 10)
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        if kind == QEvent.Type.MouseButtonRelease and hold_s:
            _t.sleep(hold_s)
        ev = QMouseEvent(kind, at, at, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        (widget.mousePressEvent if kind == QEvent.Type.MouseButtonPress else widget.mouseReleaseEvent)(ev)


def test_a_tap_on_the_pill_listens_and_the_next_tap_sends(panel):
    iv = _to_interview(panel)
    before = len(panel.voice.calls)
    _click(iv.pill)
    assert panel.voice.calls[before:] == [("hold", True)], "a click used to send a split second of silence"
    assert "tap when you're done" in iv.status.text()
    _click(iv.pill)
    assert panel.voice.calls[-1] == ("hold", False)


def test_holding_the_pill_works_like_the_keys(panel, monkeypatch):
    import ui.onboarding_steps as St
    monkeypatch.setattr(St.StatusPill, "HOLD_S", 0.01)
    iv = _to_interview(panel)
    _click(iv.pill, hold_s=0.03)
    assert panel.voice.calls[-2:] == [("hold", True), ("hold", False)]


def test_the_round_mic_sits_in_the_middle(panel):
    iv = _to_interview(panel)
    iv.pill.set_compact(True)
    m = iv.pill.layout().contentsMargins()
    assert iv.pill.width() == 44 and (m.left(), m.right()) == (0, 0), "it was off-centre and cut off"
    iv.pill.set_compact(False)
    assert iv.pill.layout().contentsMargins().left() == 16


def test_the_list_has_room_before_it_is_filled(panel):
    import ui.onboarding_steps as St
    drive = _to_step(panel, St.DriveStep)
    empty = drive.list.sizeHint().height()
    for item in ("One", "Two", "Three", "Four", "Five"):
        drive.list.box.setText(item)
        drive.list.add()
    assert drive.list.sizeHint().height() == empty, "30 Oct: filling it squashed it"
    drive.list.clear()
    assert drive.list.items() == []


def test_a_step_is_measured_once_it_has_built_itself(panel, monkeypatch):
    import ui.onboarding_steps as St
    tall = St.label("x", "obNote")
    tall.setMinimumHeight(420)
    monkeypatch.setattr(St, "_plans_page", lambda bridge: tall)
    _to_step(panel, St.PlansStep)
    assert panel.panel_rect().height() > 420, "30 Oct: the plans were squashed"
