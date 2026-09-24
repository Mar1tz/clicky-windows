"""LG-01: a step goes on screen when it is due - only its voice waits.

24 Sep, 23:03: step one was seen done at 23:03:12 and step two came up at
23:03:36. The runner held every step - its words AND its arrow - until
Tora was quiet, and the conversation was stuck in a tool loop for those 24
seconds. An arrow makes no sound; it never has to wait for one.

So now:
  * a step's words and arrow go up the moment it is due;
  * its line is said on a voice lane, when it is quiet - and not at all if
    the user has already done the step;
  * a step seen done moves on at once, busy conversation or not;
  * a click made before the line was said still counts (the step watcher
    arms itself, and the runner trusts it).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncio
import time

import pytest

from ai.gemini_planner import Guide, Step
from tutor_features.guide import GuideRunner


class Scene:
    """Scripted app: what is busy, what is done, what was shown and said."""

    def __init__(self, n=3, *, speak_s=0.0):
        self.guide = Guide(goal="g", steps=[Step(instruction=f"Step {i + 1}.", target=f"T{i + 1}")
                                            for i in range(n)])
        self.busy_until = 0.0
        self.done: set[int] = set()
        self.shown: list[tuple[int, float]] = []
        self.said: list[tuple[int, float]] = []
        self.speaking = 0
        self.most_at_once = 0
        self.speak_s = speak_s
        self.t0 = time.monotonic()

    def now(self):
        return time.monotonic() - self.t0

    async def show(self, idx):
        self.shown.append((idx, self.now()))

    async def speak(self, idx):
        self.speaking += 1
        self.most_at_once = max(self.most_at_once, self.speaking)
        self.said.append((idx, self.now()))
        await asyncio.sleep(self.speak_s)
        self.speaking -= 1

    async def check(self, step):
        return self.guide.steps.index(step) in self.done

    def busy(self):
        return time.monotonic() < self.t0 + self.busy_until

    def runner(self, **kw):
        async def say(_text):
            pass
        options = dict(show_step=self.show, speak_step=self.speak, check=self.check,
                       say=say, is_busy=self.busy, on_stop=lambda: None,
                       self_arming=lambda step: True, poll_s=0.02, confirm_polls=2,
                       present_delay_s=0.0)
        options.update(kw)
        return GuideRunner(self.guide, **options)


async def _until(check, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        await asyncio.sleep(0.01)
    return False


def test_the_step_is_on_screen_while_tora_is_still_talking():
    scene = Scene()
    scene.busy_until = 1.0                       # "sure, let me walk you through it"

    async def run():
        runner = scene.runner()
        await runner.start()
        await _until(lambda: scene.said)
        runner.stop()

    asyncio.run(run())
    (idx, shown_at), = scene.shown[:1]
    assert idx == 0 and shown_at < 0.2, f"words and arrow up at {shown_at:.2f}s"
    assert scene.said[0][0] == 0 and scene.said[0][1] >= 1.0, "the voice waited its turn"


def test_a_step_seen_done_moves_on_while_the_conversation_is_busy():
    """The 23:03 freeze: done at 23:03:12, shown at 23:03:36."""
    scene = Scene()

    async def run():
        runner = scene.runner()
        await runner.start()
        await _until(lambda: scene.shown)
        scene.busy_until = scene.now() + 30.0     # a model stuck in a tool loop
        done_at = scene.now()
        scene.done.add(0)
        assert await _until(lambda: len(scene.shown) >= 2, timeout=3.0)
        await asyncio.sleep(0.3)
        runner.stop()
        return done_at, scene.shown[1][1] - done_at

    done_at, waited = asyncio.run(run())
    assert waited < 0.5, f"step two came up {waited:.2f}s after step one was seen done"
    assert all(at < done_at for _i, at in scene.said), "nothing said over the busy conversation"


def test_a_click_made_before_the_line_was_said_still_counts():
    """The watcher latched 'done' while the line waited, so the first check
    is already True. That used to read as 'it already looked like this'."""
    scene = Scene(n=2)
    scene.busy_until = 30.0
    scene.done.add(0)                             # clicked the moment it appeared

    async def run():
        runner = scene.runner()
        await runner.start()
        ok = await _until(lambda: len(scene.shown) >= 2, timeout=2.0)
        runner.stop()
        return ok

    assert asyncio.run(run()), "the step moved on"


def test_without_a_self_arming_check_an_already_true_step_still_waits():
    """The guard itself stays for checks that do not arm themselves."""
    scene = Scene(n=2)
    scene.done.add(0)

    async def run():
        runner = scene.runner(self_arming=lambda step: False)
        await runner.start()
        moved = await _until(lambda: len(scene.shown) >= 2, timeout=0.5)
        runner.stop()
        return moved

    assert asyncio.run(run()) is False


def test_a_line_for_a_step_already_done_is_not_said():
    scene = Scene()
    scene.busy_until = 30.0
    events = []

    async def run():
        runner = scene.runner(on_event=lambda kind, detail: events.append((kind, detail)))
        await runner.start()
        await _until(lambda: scene.shown)
        scene.done.add(0)
        await _until(lambda: len(scene.shown) >= 2)
        scene.busy_until = 0.0                    # quiet at last
        await _until(lambda: scene.said)
        runner.stop()

    asyncio.run(run())
    assert [i for i, _ in scene.said] == [1], "step one was already done - only step two is said"
    assert ("step_unsaid", {"index": 0}) in events


def test_lines_are_never_said_over_each_other():
    scene = Scene(speak_s=0.4)

    async def run():
        runner = scene.runner()
        await runner.start()
        await _until(lambda: scene.said)          # step one being said...
        scene.done.add(0)                         # ...and done while it is
        await _until(lambda: len(scene.said) >= 2, timeout=3.0)
        runner.stop()

    asyncio.run(run())
    assert [i for i, _ in scene.said] == [0, 1]
    assert scene.most_at_once == 1
    assert scene.shown[1][1] < scene.said[1][1], "step two was on screen before its line"


def test_the_ending_waits_for_the_last_line():
    scene = Scene(n=1, speak_s=0.3)
    finished = []

    async def on_finish(ahead):
        finished.append((scene.speaking, scene.now()))

    async def run():
        runner = scene.runner(on_finish=on_finish)
        await runner.start()
        await _until(lambda: scene.said)
        scene.done.add(0)
        await _until(lambda: finished, timeout=3.0)

    asyncio.run(run())
    assert finished and finished[0][0] == 0, "'that's it, done' is never said over the step"


# ── the manager: showing makes no sound ──────────────────────────────────────

def test_showing_a_step_says_nothing(monkeypatch):
    from types import SimpleNamespace

    from companion_manager import CompanionManager

    spoken, drawn = [], []
    mgr = CompanionManager.__new__(CompanionManager)
    plan = Guide(goal="g", steps=[Step(instruction="Click Captions.", target="Captions")])
    mgr._lesson_plan = plan
    mgr._plan_in_progress = None
    mgr._lesson_step_idx = -1
    mgr._guide_runner = None
    mgr._step_audio = {}
    mgr._submit = lambda coro: coro.close()
    mgr._make_step_watcher = lambda step, next_target="": SimpleNamespace(
        begin=lambda: asyncio.sleep(0), end=lambda: None)
    mgr._activity = lambda *_a: None
    mgr._emit_state = lambda *_a: None
    mgr._get_tts = lambda: SimpleNamespace(speak=lambda *a, **k: spoken.append(a))
    for sig in ("sig_guide_progress", "sig_response_chunk", "sig_response_done",
                "sig_guide_release", "sig_clear_drawings"):
        setattr(mgr, sig, SimpleNamespace(emit=lambda *a, _s=sig: drawn.append(_s)))

    asyncio.run(mgr._display_step(0))
    assert "sig_response_chunk" in drawn and mgr._lesson_step_idx == 0
    assert spoken == [], "on screen, not said: the voice lane says it when it is quiet"
    # LG-02: the last step's arrow and box come down before this one's go up.
    assert drawn.index("sig_guide_release") < drawn.index("sig_response_chunk")
    assert "sig_clear_drawings" in drawn


# ── LG-02: the arrow gets there in a blink, and stays ────────────────────────

@pytest.fixture(scope="module")
def overlay():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.overlay import CursorOverlay
    o = CursorOverlay()
    yield o
    o.set_slow_mode(False)
    app.processEvents()


def test_a_walkthrough_arrow_gets_there_in_a_blink(overlay):
    from ui.overlay import FLY_DURATION_MIN, GUIDE_FLY_S
    overlay.set_slow_mode(False)
    overlay.guide_point(500.0, 300.0, "Captions")
    assert overlay._fly_duration == pytest.approx(GUIDE_FLY_S) and GUIDE_FLY_S <= 0.4
    assert FLY_DURATION_MIN > 1.0, "teacher pace stays for pointing while Tora explains"


def test_it_stays_on_the_target_until_the_step_changes(overlay):
    overlay.guide_point(500.0, 300.0, "Captions")
    overlay._fly_t0 -= 5.0                        # the flight is over
    overlay._tick()
    assert overlay._flight_phase == "dwelling"
    assert overlay._dwell_until == float("inf"), "not 4 s and back to the cursor"
    overlay.guide_release()                       # the step changed
    overlay._tick()
    assert overlay._flight_phase == "returning"


def test_slow_mode_doubles_the_blink(overlay):
    from ui.overlay import GUIDE_FLY_S
    overlay.set_slow_mode(True)
    try:
        overlay.guide_point(500.0, 300.0, "Captions")
        assert overlay._fly_duration == pytest.approx(2 * GUIDE_FLY_S)
    finally:
        overlay.set_slow_mode(False)


def test_pointing_while_tora_explains_keeps_teacher_pace(overlay):
    from ui.overlay import FLY_DURATION_MIN
    overlay.guide_release()
    overlay.point_at(100.0, 100.0, "Save")
    assert overlay._fly_duration >= FLY_DURATION_MIN


def test_a_steps_arrow_is_a_walkthrough_arrow():
    """_point_step_target flies the quick, held arrow - not point_at."""
    from types import SimpleNamespace

    from companion_manager import CompanionManager

    mgr = CompanionManager.__new__(CompanionManager)
    plan = Guide(goal="g", steps=[Step(instruction="Click Captions.", target="Captions")])
    mgr._lesson_plan, mgr._lesson_step_idx = plan, 0
    mgr._step_point, mgr._guide_stats = {}, None
    mgr._activity = lambda *_a: None
    emitted = []

    async def resolve(query, **kw):
        return SimpleNamespace(x=10.0, y=20.0, label=query, via="uia", bbox=(0, 0, 20, 40))

    mgr._resolve_point = resolve
    for sig in ("sig_guide_point", "sig_point_at", "sig_draw", "sig_point_hold"):
        setattr(mgr, sig, SimpleNamespace(emit=lambda *a, _s=sig: emitted.append(_s)))
    mgr._remember_ref = lambda *a: None
    asyncio.run(mgr._point_step_target(0, plan.steps[0], note=False))
    assert "sig_guide_point" in emitted and "sig_point_at" not in emitted
    assert "sig_point_hold" not in emitted, "held by the overlay itself, not the speech"


def test_step_ones_early_arrow_stays_up_when_step_one_is_shown():
    """Step one is pointed at the moment it is planned (GW-08) - often
    before the runner shows it. Showing it must not take that arrow down."""
    from types import SimpleNamespace

    from companion_manager import CompanionManager

    drawn = []
    mgr = CompanionManager.__new__(CompanionManager)
    plan = Guide(goal="g", steps=[Step(instruction="Click Captions.", target="Captions"),
                                  Step(instruction="Click Generate.", target="Generate")])
    mgr._lesson_plan, mgr._plan_in_progress = plan, None
    mgr._lesson_step_idx, mgr._guide_runner, mgr._step_audio = -1, None, {}
    mgr._submit = lambda coro: coro.close()
    mgr._make_step_watcher = lambda step, next_target="": SimpleNamespace(
        begin=lambda: asyncio.sleep(0), end=lambda: None)
    mgr._activity = lambda *_a: None
    for sig in ("sig_guide_progress", "sig_response_chunk", "sig_response_done",
                "sig_guide_release", "sig_clear_drawings"):
        setattr(mgr, sig, SimpleNamespace(emit=lambda *a, _s=sig: drawn.append(_s)))

    mgr._arrow_for = (id(plan), 0)            # step one's arrow is already up
    asyncio.run(mgr._display_step(0))
    assert "sig_guide_release" not in drawn and "sig_clear_drawings" not in drawn
    asyncio.run(mgr._display_step(1))         # step two: step one's arrow comes down
    assert "sig_guide_release" in drawn and mgr._arrow_for is None
