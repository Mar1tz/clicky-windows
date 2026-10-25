"""Show it by tracing, TF-01 and TF-02 (docs/TRACE_TO_FOCUS.md).

Asked for on 10 Oct 2026: "when I'm holding down the shortcut it can use my
cursor to move around and it leaves a little ghost trail behind it of like
mist - and then the AI must know to focus on this area." The path is
recorded while the shortcut is held (ai/trace.py), and the overlay leaves a
mist in Tora's colour behind the cursor (ui/mist.py). When it is let go,
the traced area stays as a faint haze until she answers. A tap leaves
nothing.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from types import SimpleNamespace

import pytest

from ai import trace as tracing


# -- TF-01: the record -------------------------------------------------------------------------------------------

def test_a_tremble_is_not_a_move():
    t = tracing.Trace()
    assert t.add(0.0, 100, 100)
    assert not t.add(0.016, 101, 100.5), "under two pixels: the hand trembled"
    assert t.add(0.032, 110, 100) and len(t.points) == 2


def test_a_mouse_that_sat_still_showed_nothing():
    t = tracing.Trace()
    for i in range(30):
        t.add(i * 0.016, 500 + (i % 2) * 3, 300)
    assert not t.moved and t.summary() == "no trace"


def test_a_sweep_is_a_trace_with_its_area_and_time():
    t = tracing.Trace()
    for i in range(60):
        t.add(i * 0.016, 100 + i * 5, 200 + 20 * math.sin(i / 6))
    assert t.moved and t.bounds()[0] == 100 and t.bounds()[2] == 395
    assert "60 points" in t.summary() and "295x" in t.summary()


def test_a_long_hold_is_capped():
    t = tracing.Trace()
    for i in range(tracing.MAX_POINTS + 500):
        t.add(i * 0.016, float(i * 3), 0.0)
    assert len(t.points) == tracing.MAX_POINTS


def test_straight_runs_are_thinned_and_corners_kept():
    t = tracing.Trace()
    for i in range(50):
        t.add(i * 0.01, i * 4.0, 0.0)              # along
    for i in range(1, 50):
        t.add(0.5 + i * 0.01, 196.0, i * 4.0)      # then down
    thin = t.simplified()
    assert len(thin) == 3 and (thin[1].x, thin[1].y) == (196.0, 0.0)


# -- TF-02: the mist ---------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _sweep(m, frames=40, step=12.0, t0=100.0):
    for i in range(frames):
        m.tick(50 + i * step, 200, t0 + i / 60)
    return t0 + frames / 60


def test_a_move_leaves_a_ribbon_of_mist_never_dots(qapp):
    from ui import mist
    m = mist.Mist(seed=1)
    m.start(100.0)
    _sweep(m, frames=10, step=40.0)                 # fast: 40 px a frame
    xs = sorted(p.x for p in m.puffs)
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    assert len(m.puffs) >= 40 and max(gaps) < mist.SPACING_PX * 2, "filled in between frames"


def test_mist_fades_and_is_gone_after_its_life(qapp):
    from ui import mist
    m = mist.Mist(seed=1)
    m.start(100.0)
    end = _sweep(m)
    m.stop(end, keep=False)
    assert m.puffs
    m.tick(0, 0, end + mist.LIFE_S + 0.01)
    assert not m.puffs and not m.active(), "a tap's trail just fades: no haze"


def test_never_more_than_the_cap(qapp):
    from ui import mist
    m = mist.Mist(seed=1)
    m.start(100.0)
    _sweep(m, frames=200, step=30.0)
    assert len(m.puffs) <= mist.MAX_PUFFS


def test_letting_go_leaves_a_haze_until_she_answers(qapp):
    from ui import mist
    m = mist.Mist(seed=1)
    m.start(100.0)
    end = _sweep(m)
    m.stop(end, keep=True)
    m.tick(0, 0, end + 3.0)
    assert m.haze and m.active(), "the traced area stays, after the moving mist has gone"
    m.end_haze(end + 3.0)
    m.tick(0, 0, end + 3.0 + mist.HAZE_FADE_S + 0.01)
    assert not m.haze, "she answered: it fades"


def test_a_haze_never_outstays_a_missing_answer(qapp):
    from ui import mist
    m = mist.Mist(seed=1)
    m.start(100.0)
    end = _sweep(m)
    m.stop(end, keep=True)
    m.tick(0, 0, end + mist.HAZE_MAX_S + 0.1)
    m.tick(0, 0, end + mist.HAZE_MAX_S + mist.HAZE_FADE_S + 0.2)
    assert not m.haze


def test_the_mist_is_drawn_in_toras_colour(qapp):
    from PyQt6.QtGui import QColor, QImage, QPainter
    from ui import buddy, mist
    m = mist.Mist(seed=1)
    m.start(100.0)
    end = _sweep(m, frames=20, step=8.0)
    img = QImage(400, 400, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    m.draw(p, end)
    p.end()
    hue = buddy.BLUE.hslHue()
    tinted = [img.pixelColor(x, 200) for x in range(60, 200, 6) if img.pixelColor(x, 200).alpha() > 20]
    assert tinted and all(abs(c.hslHue() - hue) < 25 for c in tinted if c.hslSaturation() > 40)
    b = m.bounds()
    assert b.left() < 50 and b.right() > 50 + 19 * 8, "its bounds cover every puff"


# -- the overlay -------------------------------------------------------------------------------------------------

def test_the_overlay_trails_while_held_and_hazes_until_the_answer(qapp):
    from ui.overlay import MODE_SPEAKING, MODE_THINKING, CursorOverlay
    o = CursorOverlay()
    try:
        o._tick_timer.stop()
        o.set_tracing("on")
        assert o._mist.tracing
        for i in range(20):
            o._mist.tick(100 + i * 10, 100, 50.0 + i / 60)
        o.set_tracing("keep")
        assert not o._mist.tracing and o._mist.haze
        assert any(r.contains(o._mist.bounds()) or r.intersects(o._mist.bounds()) for r in o._regions() or [])
        o.set_mode(MODE_THINKING)
        assert o._mist._haze_fade_at is None, "still thinking: the haze stays"
        o.set_mode(MODE_SPEAKING)
        assert o._mist._haze_fade_at is not None, "answering: it fades"
        o.set_tracing("on")
        o.set_tracing("drop")
        assert not o._mist.haze, "a tap: no haze"
    finally:
        o._tick_timer.stop()
        o.deleteLater()


# -- the manager: only while the shortcut is held ------------------------------------------------------------

def _manager(monkeypatch, held_s=2.0, moved=True):
    import companion_manager as cm
    from ui.panel import AppState
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    sent = []
    mgr.sig_trace = SimpleNamespace(emit=sent.append)
    mgr.sig_hands_free = SimpleNamespace(emit=lambda on: sent.append(("hands_free", on)))
    mgr._state = AppState.LISTENING
    mgr._claim_end = lambda: False
    mgr._submit = lambda coro: coro.close()
    mgr._hotkey_pressed_at = cm.time.monotonic() - held_s
    mgr._trace = tracing.Trace()
    for i in range(30):
        mgr._trace.add(i * 0.016, 100 + (i * 6 if moved else i % 2), 100)
    mgr._trace_live = True
    return mgr, sent


def test_a_hold_that_showed_something_keeps_its_haze(monkeypatch, caplog):
    mgr, sent = _manager(monkeypatch)
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        mgr.on_hotkey_release()
    assert sent == ["keep"] and mgr._trace is not None and not mgr._trace_live
    assert "trace: 30 points" in caplog.text


def test_a_tap_leaves_no_trail(monkeypatch):
    mgr, sent = _manager(monkeypatch, held_s=0.05)
    mgr.on_hotkey_release()
    assert sent[0] == "drop" and mgr._trace is None and ("hands_free", True) in sent


def test_a_hold_with_a_still_mouse_shows_nothing(monkeypatch):
    mgr, sent = _manager(monkeypatch, moved=False)
    mgr.on_hotkey_release()
    assert sent == ["drop"] and mgr._trace is None


def test_the_mouse_is_sampled_until_the_shortcut_comes_up(monkeypatch):
    import companion_manager as cm
    mgr, _sent = _manager(monkeypatch)
    trace = tracing.Trace()
    mgr._trace = trace
    spots = iter([(10.0, 10.0), (40.0, 10.0), (80.0, 30.0)])

    def cursor():
        try:
            return next(spots)
        except StopIteration:
            mgr._trace_live = False                     # the shortcut comes up
            return None
    monkeypatch.setattr(cm.look_region, "cursor_position", cursor)
    monkeypatch.setattr(mgr, "TRACE_EVERY_S", 0.0)
    asyncio.run(mgr._record_trace(trace, cm.time.monotonic()))
    assert [(p.x, p.y) for p in trace.points] == [(10.0, 10.0), (40.0, 10.0), (80.0, 30.0)]


# -- TF-03: from a trail to what they meant ----------------------------------------------------------------------

def _drawn(*segments):
    t, clock = tracing.Trace(), 0.0
    for seg in segments:
        if seg == "pause":
            clock += 0.6
            continue
        for x, y in seg:
            clock += 0.016
            t.add(clock, x, y)
    return t


CIRCLE = [(400 + 90 * math.cos(a / 40 * math.tau), 300 + 50 * math.sin(a / 40 * math.tau)) for a in range(43)]
OPEN_CIRCLE = [(400 + 90 * math.cos(a / 40 * math.tau * 0.85), 300 + 50 * math.sin(a / 40 * math.tau * 0.85))
               for a in range(40)]
UNDERLINE = [(200 + i * 8, 500 + (i % 3)) for i in range(40)]
SWEEP = [(300 + (i % 10) * 15 * (1 if (i // 10) % 2 == 0 else -1) + (0 if (i // 10) % 2 == 0 else 150), 200 + i * 2)
         for i in range(60)]
TRAVEL = [(100 + i * 12, 100 + i * 6) for i in range(30)]
TRAVEL2 = [(460 + i * 10, 280 - i * 4) for i in range(25)]


@pytest.mark.parametrize("segments, kinds", [
    ((CIRCLE,), ["circle"]),
    ((OPEN_CIRCLE,), ["circle"]),                         # a circle not quite closed is still one
    ((UNDERLINE,), ["underline"]),
    ((SWEEP,), ["sweep"]),
    ((TRAVEL, "pause"), ["point"]),                       # moved to a thing and stopped: that thing
    ((TRAVEL, "pause", TRAVEL2, "pause"), ["point", "point"]),   # "this... and this"
    (([(50, 50)], "pause", CIRCLE), ["circle"]),          # where the mouse sat first is not a gesture
])
def test_what_a_trail_means(segments, kinds):
    assert [f.kind for f in tracing.focuses(_drawn(*segments))] == kinds


def test_an_underline_reads_the_words_just_above_it():
    (f,) = tracing.focuses(_drawn(UNDERLINE))
    assert all(y < 500 for _x, y in f.samples) and f.rect[1] < 480


def test_this_and_this_keep_their_order_and_times():
    a, b = tracing.focuses(_drawn(TRAVEL, "pause", TRAVEL2, "pause"))
    assert a.t1 < b.t0 and a.samples[0][0] < b.samples[0][0]


# -- TF-04: what is under it, read --------------------------------------------------------------------------------

class _Range:
    def __init__(self, text):
        self.text, self.unit = text, None

    def ExpandToEnclosingUnit(self, unit, waitTime=0.5):
        assert waitTime == 0, "never the library's half-second wait"
        self.unit = unit

    def GetText(self, n=-1):
        return self.text


def _fake_uia(monkeypatch, at):
    """`at(x, y)` -> (type, name, text, password) of what is under a point."""
    import sys

    class Ctrl:
        def __init__(self, x, y):
            self.ControlTypeName, self.Name, self._text, self.IsPassword = at(x, y)

        def GetPattern(self, pid):
            return SimpleNamespace(RangeFromPoint=lambda x, y: _Range(self._text)) if self._text else None

        def GetParentControl(self):
            return None

        def GetTopLevelControl(self):
            return SimpleNamespace(Name="Report.docx - Word")

    fake = SimpleNamespace(ControlFromPoint=lambda x, y: Ctrl(x, y),
                           PatternId=SimpleNamespace(TextPattern=10014),
                           TextUnit=SimpleNamespace(Word=2, Line=3, Paragraph=4),
                           InitializeUIAutomationInCurrentThread=lambda: None)
    monkeypatch.setitem(sys.modules, "uiautomation", fake)
    from ai import trace_read
    monkeypatch.setattr(trace_read, "_window_at", lambda x, y: "Report.docx - Word")   # asked of Windows


def test_an_underline_is_read_word_for_word(monkeypatch):
    from ai import trace_read
    _fake_uia(monkeypatch, lambda x, y: ("TextControl", "", "Total due within 30 days of invoice.", False))
    (r,) = trace_read.read(tracing.focuses(_drawn(UNDERLINE)))
    assert r.text == "Total due within 30 days of invoice." and r.window == "Report.docx - Word"
    assert not r.is_picture()
    line = trace_read.voice_line([r])
    assert 'underlined "Total due within 30 days of invoice."' in line and "no need to look" in line


def test_a_button_is_named_and_a_picture_wants_a_look(monkeypatch):
    from ai import trace_read
    _fake_uia(monkeypatch, lambda x, y: ("ImageControl", "Revenue chart", "", False) if x > 600
              else ("ButtonControl", "Export", "", False))
    a, b = trace_read.read(tracing.focuses(_drawn(TRAVEL, "pause", TRAVEL2, "pause")))
    assert a.things == ['button "Export"'] and b.things == ['chart "Revenue chart"']
    line = trace_read.voice_line([a, b])
    assert "(1) pointed at" in line and "(2) pointed at" in line and "in that order" in line
    assert "look_at_screen sees the traced area" in line


def test_never_a_password_field(monkeypatch):
    from ai import trace_read
    _fake_uia(monkeypatch, lambda x, y: ("EditControl", "Password", "hunter2", True))
    (r,) = trace_read.read(tracing.focuses(_drawn(UNDERLINE)))
    assert r.text == "" and r.things == []


def test_reading_stops_at_its_budget(monkeypatch):
    import time as _time
    from ai import trace_read

    def slow(x, y):
        _time.sleep(0.05)
        return ("TextControl", "", f"line at {x:.0f}", False)
    _fake_uia(monkeypatch, slow)
    started = _time.monotonic()
    trace_read.read(tracing.focuses(_drawn(SWEEP)), budget_s=0.1)
    assert _time.monotonic() - started < 0.3


# -- TF-05: the voice is told before its turn ends --------------------------------------------------------------

def _bare(monkeypatch):
    import companion_manager as cm
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._privacy_guard = False
    mgr._trace = _drawn(UNDERLINE)
    monkeypatch.setattr(cm, "active_window_title", lambda: "Report.docx - Word")
    return mgr, cm


class _Conv:
    def __init__(self):
        self.sent = []

    async def send_context(self, line):
        self.sent.append(line)


def test_the_trace_line_reaches_the_voice(monkeypatch):
    mgr, _cm = _bare(monkeypatch)
    _fake_uia(monkeypatch, lambda x, y: ("TextControl", "", "Total due within 30 days.", False))
    conv = _Conv()

    async def go():
        await mgr._start_trace_read(mgr._trace)
        await mgr._send_trace_line(conv)
    asyncio.run(go())
    assert len(conv.sent) == 1 and 'underlined "Total due within 30 days."' in conv.sent[0]
    assert mgr._trace_task is None, "once per question"


def test_a_late_reading_still_says_where_and_what_shape(monkeypatch):
    mgr, cm = _bare(monkeypatch)
    monkeypatch.setattr(mgr, "TRACE_WAIT_S", 0.05)
    conv = _Conv()

    async def never(_trace):
        await asyncio.sleep(10)
    monkeypatch.setattr(mgr, "_read_trace", never)

    async def go():
        await mgr._start_trace_read(mgr._trace)
        await mgr._send_trace_line(conv)
    asyncio.run(go())
    assert conv.sent and "underlined an area with no text to read" in conv.sent[0]


def test_a_sensitive_window_is_never_read(monkeypatch):
    mgr, cm = _bare(monkeypatch)
    mgr._privacy_guard = True
    monkeypatch.setattr(cm, "is_sensitive_window", lambda title: True)
    _fake_uia(monkeypatch, lambda x, y: ("TextControl", "", "Account 12345", False))
    line = asyncio.run(mgr._read_trace(mgr._trace))
    assert "Account 12345" not in line and "underlined" in line


def test_no_trace_no_line(monkeypatch):
    mgr, _cm = _bare(monkeypatch)
    conv = _Conv()
    asyncio.run(mgr._send_trace_line(conv))
    assert conv.sent == []


def test_the_voice_has_the_rule():
    import config
    rules = " ".join(config._REALTIME_RULES.split())
    assert "A TRACE IS WHAT THEY MEAN" in rules and "in the order they traced it" in rules


def test_the_look_is_told_where_on_its_image_they_traced():
    from ai import look_region
    frame = (1000, 0, 1920, 1080)                       # the image: a 1920x1080 monitor at x 1000
    one = look_region.trace_hint([(1960, 540, 2440, 810)], frame)
    assert "x 500-750, y 500-750" in one and "this area" in one
    two = look_region.trace_hint([(1100, 100, 1300, 200), (2500, 900, 2700, 1000)], frame)
    assert "(1) x" in two and "(2) x" in two and "in this order" in two
    assert look_region.trace_hint([(10, 10, 50, 50)], frame) == "", "not on this image: nothing said"


# -- fixed after the owner's first try (10 Oct, 23:23) -----------------------------------------------------------
#
# They drew round something and it "isn't working at all". The live session
# answered on its own end-of-speech detection (200 ms of silence) while the
# shortcut was still held and the circle not finished, and the local watchdog
# ended the capture - so the trace was dropped at the release. And the look
# was only told where the trace was; now it sees the traced area itself.

def test_the_look_photographs_the_traced_area_with_room_round_it():
    from ai import look_region
    monitor = (0, 0, 1920, 1080)
    left, top, w, h = look_region.trace_crop([(800, 400, 1100, 600)], monitor)
    assert left < 800 and top < 400 and left + w > 1100 and top + h > 600, "the traced area, and around it"
    assert w >= 360 and h >= 220
    small = look_region.trace_crop([(10, 10, 30, 30)], monitor)
    assert small[0] == 0 and small[1] == 0 and small[2] >= 300, "kept on the monitor, still big enough to read"
    assert look_region.trace_crop([(3000, 10, 3100, 50)], monitor) is None


class _LiveConv:
    def __init__(self):
        self.ready, self.log = True, []

    async def push_audio(self, chunk):
        self.log.append(("audio", chunk))

    async def send_context(self, line):
        self.log.append(("context", line))

    async def end_user_audio(self, stop=None):
        self.log.append(("end",))

    def release_superseded(self):
        pass


def _streaming_manager(monkeypatch):
    import companion_manager as cm
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    conv = _LiveConv()
    mgr._realtime = conv
    mgr._rt_streaming = True
    mgr._rt_audio = []
    mgr._key_down = True
    mgr._last_rms = 0.0

    async def screen():
        return "Screen: Report.docx"
    mgr._screen_context = screen
    return mgr, conv


def test_while_held_a_pause_is_not_sent_as_the_end(monkeypatch):
    mgr, conv = _streaming_manager(monkeypatch)
    monkeypatch.setattr(mgr, "HOLD_QUIET_S", 0.05)
    loud, quiet = b"S" * 320, b"\0" * 320

    async def speak():
        for rms, block, n in ((0.5, loud, 3), (0.0, quiet, 12), (0.5, loud, 2)):
            for _ in range(n):
                mgr._last_rms = rms
                mgr._rt_audio.append(block)
                await asyncio.sleep(0.02)
        mgr._key_down = False                       # let go
        mgr._rt_streaming = False

    async def go():
        await asyncio.gather(mgr._realtime_stream(), speak())
    asyncio.run(go())
    audio = b"".join(entry[1] for entry in conv.log if entry[0] == "audio")
    assert audio.count(loud) == 5, "every word reached it"
    assert audio.count(quiet) == 12, "the pause too - but only once the words went on"
    sent = [entry[0] for entry in conv.log]
    assert sent[-1] == "end"


def test_while_held_the_pause_before_the_release_is_never_heard(monkeypatch):
    mgr, conv = _streaming_manager(monkeypatch)
    monkeypatch.setattr(mgr, "HOLD_QUIET_S", 0.05)

    async def speak_then_draw():
        mgr._last_rms = 0.5
        mgr._rt_audio.append(b"S" * 320)
        await asyncio.sleep(0.03)
        for _ in range(15):                          # quiet, finishing the circle
            mgr._last_rms = 0.0
            mgr._rt_audio.append(b"\0" * 320)
            await asyncio.sleep(0.02)
        quiet_sent = sum(1 for e in conv.log if e[0] == "audio" and e[1].startswith(b"\0"))
        assert quiet_sent <= 4, "no 200 ms of silence while the shortcut is down"
        mgr._key_down = False
        mgr._rt_streaming = False

    async def go():
        await asyncio.wait_for(asyncio.gather(mgr._realtime_stream(), speak_then_draw()), 5)
    asyncio.run(go())


def test_the_trace_goes_before_the_end_of_the_question(monkeypatch):
    mgr, conv = _streaming_manager(monkeypatch)
    mgr._key_down = False
    mgr._trace = _drawn(UNDERLINE)

    async def go():
        async def line():
            return "While asking, they traced with the mouse: underlined \"Total due\"."
        mgr._trace_task = asyncio.ensure_future(line())
        mgr._rt_streaming = False
        await mgr._realtime_stream()
    asyncio.run(go())
    kinds = [e[0] for e in conv.log]
    assert kinds.index("context", 1) < kinds.index("end"), "the trace, then the end"


def test_the_watchdog_never_ends_a_held_question_on_silence(monkeypatch):
    import time as _time
    import companion_manager as cm
    from ui.panel import AppState
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._state = AppState.LISTENING
    mgr._last_rms = 0.0
    mgr._key_down = True
    mgr._claim_end = lambda: False
    monkeypatch.setattr(mgr, "_NO_SPEECH_S", 0.05)
    monkeypatch.setattr(mgr, "_END_SILENCE_S", 0.05)
    monkeypatch.setattr(mgr, "_MAX_UTTERANCE_S", 0.4)
    started = _time.monotonic()
    asyncio.run(mgr._auto_stop_on_silence())
    assert _time.monotonic() - started >= 0.35, "held: only the release (or the hard cap) ends it"


def test_a_traced_question_looks_at_the_traced_area(monkeypatch):
    import companion_manager as cm
    import tutor
    from ai import gemini_vision, look_region, trace_read
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._drawn = []
    mgr.sig_draw = SimpleNamespace(emit=mgr._drawn.append)
    mgr.sig_clear_drawings = SimpleNamespace(emit=lambda: None)
    mgr._privacy_guard = False
    mgr._activity = lambda what: None
    mgr._shape_from_tag = lambda tag: {"tag": tag}
    shot = SimpleNamespace(base64_jpeg="whole", physical_left=0, physical_top=0, physical_width=1920,
                           physical_height=1080, index=0)

    async def capture(**_kw):
        return [shot]
    mgr._capture_shots = capture
    mgr._active_shot = lambda screens: shot
    mgr._trace_readings = [trace_read.Reading(tracing.Focus("circle", (800, 400, 1100, 600), 0.1, 1.0, []))]
    monkeypatch.setattr(tutor, "foreground_window_rect", lambda: None)
    monkeypatch.setattr(look_region, "cursor_position", lambda: (950.0, 500.0))
    grabbed = []
    monkeypatch.setattr(cm, "capture_region", lambda *a: grabbed.append(a[:4]) or ("CROP",))
    monkeypatch.setattr(cm, "active_window_title", lambda: "Report.docx - Word")
    seen = {}

    async def fake_look(question, image, **kw):
        seen.update(image=image, **kw)
        return gemini_vision.Look(answer="That paragraph says the total is due in 30 days.", sure=True, path="fast",
                                  model="flash", actions=[])
    monkeypatch.setattr(gemini_vision, "look", fake_look)
    asyncio.run(mgr._tool_look_at_screen({"question": "what does this say?"}))
    assert seen["image"] == "CROP", "the traced area, not the whole window"
    assert grabbed and grabbed[-1][0] < 800 and grabbed[-1][0] + grabbed[-1][2] > 1100
    assert "traced over this area" in seen["context"]


# -- WR-02: each stroke is read while the shortcut is still held ------------------------------------------------

def test_a_stroke_is_read_while_held_and_not_again_at_the_release(monkeypatch):
    import companion_manager as cm
    from ai import trace_read
    mgr, _sent = _manager(monkeypatch)
    mgr._privacy_guard = False
    monkeypatch.setattr(cm, "active_window_title", lambda: "Report.docx - Word")
    read = []

    def reader(focuses, budget_s=0.3):
        read.append([f.kind for f in focuses])
        return [trace_read.Reading(f, text="Total due within 30 days.") for f in focuses]
    monkeypatch.setattr(cm.trace_read, "read", reader)
    path = [(100.0 + 8 * i, 300.0) for i in range(30)]          # a line under some words, then a rest
    started = cm.time.monotonic()

    def cursor():
        if path:
            return path.pop(0)
        if cm.time.monotonic() - started > 0.9:
            mgr._trace_live = False                          # the shortcut comes up
        return (332.0, 300.0)
    monkeypatch.setattr(cm.look_region, "cursor_position", cursor)
    monkeypatch.setattr(mgr, "TRACE_EVERY_S", 0.01)

    async def go():
        mgr._start_trace()                                   # its own sampler is not run: this one is
        await mgr._record_trace(mgr._trace, started)
        assert read == [["underline"]], "read once the mouse rested, before the release"
        return await mgr._read_trace(mgr._trace)
    line = asyncio.run(go())
    assert read == [["underline"]], "nothing read twice"
    assert 'underlined "Total due within 30 days."' in line


def test_a_late_reading_keeps_what_was_read_while_held(monkeypatch):
    mgr, cm = _bare(monkeypatch)
    monkeypatch.setattr(mgr, "TRACE_WAIT_S", 0.05)
    (focus,) = tracing.focuses(mgr._trace)
    mgr._trace_ahead = {mgr._focus_key(focus): cm.trace_read.Reading(focus, text="Total due within 30 days.")}
    conv = _Conv()

    async def never(_trace):
        await asyncio.sleep(10)
    monkeypatch.setattr(mgr, "_read_trace", never)

    async def go():
        await mgr._start_trace_read(mgr._trace)
        await mgr._send_trace_line(conv)
    asyncio.run(go())
    assert conv.sent and 'underlined "Total due within 30 days."' in conv.sent[0]


def test_the_release_says_what_went_with_the_question():
    """PT-07: "Pointing context attached" under the haze for a moment, and
    the haze stays until she answers - a look can take 20 s."""
    from ui import mist as mist_mod
    m = mist_mod.Mist(seed=1)
    m.start(0.0)
    for i in range(40):
        m._trail_to(100 + i * 5, 200 + (i % 7), 0.0)
    m.stop(1.0, keep=True)
    assert m.cue_showing(1.5) and not m.cue_showing(1.0 + mist_mod.CUE_S + 0.1)
    cue = m.cue_rect()
    assert cue is not None and cue.top() > max(y for _, y, _ in m.haze), "under what was shown"
    assert m.bounds().contains(cue), "repainted with it"
    m.tick(0, 0, 1.0 + 20.0)
    assert m.haze, "a slow answer still finds it there"
    m.end_haze(21.5)
    assert not m.cue_showing(21.6)


def test_the_trail_can_be_a_line_instead(monkeypatch):
    """PT-03: a setting draws the path as one smooth rounded line; the haze,
    the cue and their timing are the same, and mist stays the default."""
    import config
    from ui import mist as mist_mod
    assert config.cfg.SWITCHES["trail_line"][1] is False, "mist is the default"
    m = mist_mod.Mist(seed=1)
    m.start(0.0, "line")
    for i in range(40):
        m._trail_to(100 + i * 9, 200 + (i % 5), i * 0.016)
    assert m.line and not m.puffs, "a line, not puffs"
    assert len(m.line) == len(m._path)
    m.stop(1.0, keep=True)
    assert m.line and m.cue_showing(1.2) and m.bounds() is not None
    m.end_haze(5.0)
    m.tick(0, 0, 5.0 + mist_mod.HAZE_FADE_S + 0.1)
    assert not m.line and not m.active(), "gone with the haze once she answers"
    m.start(10.0, "line")
    for i in range(10):
        m._trail_to(100 + i * 9, 200, 10.0 + i * 0.016)
    m.stop(10.5, keep=False)                       # a tap
    m.tick(0, 0, 10.5 + mist_mod.HAZE_FADE_S + 0.1)
    assert not m.line


def test_the_line_switch_is_saved_and_read_where_it_is_used(monkeypatch):
    import config
    from ui.app_bridge import AppBridge
    written = {}
    monkeypatch.setattr(config.cfg, "_write_env", lambda k, v: written.update({k: v}))
    monkeypatch.delenv("TORA_TRAIL_LINE", raising=False)
    br = AppBridge.__new__(AppBridge)
    br.tray = None
    br.changed = type("S", (), {"emit": lambda self, *a: None})()
    br.set_switch("trail_line", True)
    assert written == {"TORA_TRAIL_LINE": "1"} and config.cfg.switch("trail_line")
    monkeypatch.delenv("TORA_TRAIL_LINE", raising=False)
