"""APP-23: a walkthrough's arrow arrives with its step, not three steps later.

24 Sep, "add captions in CapCut": every step's words were up in ~3 s, and
every arrow came 16-29 s later - step one's while the user was on step
three. CapCut's accessibility tree names one control, so for each target:

  * the UIA walk ran far past its 1.2 s cap - the deadline was checked every
    32 nodes, and a busy app answers each COM call slowly;
  * OCR read monitors[1], which on a three-screen PC is the right-hand
    screen, not the one CapCut was on - and added no monitor offset, so a
    hit on any screen but the one at (0, 0) would have pointed elsewhere;
  * four OCRs ran at once (step one was looked up twice), each ~4x slower;
  * and only after all that failed was the vision model - which found each
    target in ~3.6 s - even asked;
  * then the arrow was drawn anyway, for a step already over.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from ai import hybrid_pointer

# The three screens of the PC in the field log, as mss lists them: [1] is the
# right-hand one, the primary is in the middle.
MONITORS = [
    {"left": 1920, "top": 0, "width": 1920, "height": 1080, "is_primary": False},
    {"left": 0, "top": 0, "width": 1920, "height": 1080, "is_primary": True},
    {"left": -1920, "top": 0, "width": 1920, "height": 1080, "is_primary": False},
]


class _Rect:
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top


# ── OCR reads the screen the app is on ───────────────────────────────────────

def test_ocr_reads_the_monitor_the_app_is_on(monkeypatch):
    monkeypatch.setattr(hybrid_pointer, "_monitors", lambda: [dict(m) for m in MONITORS])
    capcut = SimpleNamespace(BoundingRectangle=_Rect(-1900, 20, -100, 1000))
    monkeypatch.setattr(hybrid_pointer, "_pointing_root", lambda hwnd=None: capcut)
    assert hybrid_pointer._monitor_for()["left"] == -1920, "the left screen, where CapCut is"


def test_with_no_window_to_go_by_it_is_the_primary_not_monitors_1(monkeypatch):
    import tutor
    monkeypatch.setattr(hybrid_pointer, "_monitors", lambda: [dict(m) for m in MONITORS])

    def _no_root(hwnd=None):
        raise RuntimeError("no UIA")

    monkeypatch.setattr(hybrid_pointer, "_pointing_root", _no_root)
    monkeypatch.setattr(tutor, "foreground_window_center", lambda: None)
    assert hybrid_pointer._monitor_for()["is_primary"] is True


class _FakeOCR:
    """RapidOCR's shape: [[box corners, text, score as a STRING]], None."""

    def __init__(self, text="Captions", delay=0.0):
        self.text, self.delay, self.calls, self.running, self.most = text, delay, 0, 0, 0
        self._lock = threading.Lock()

    def __call__(self, img):
        with self._lock:
            self.calls += 1
            self.running += 1
            self.most = max(self.most, self.running)
        time.sleep(self.delay)
        with self._lock:
            self.running -= 1
        return [[[[30.0, 81.0], [81.0, 81.0], [81.0, 92.0], [30.0, 92.0]],
                 self.text, "0.93"]], None


class _FakeMSS:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def grab(self, region):
        w, h = 200, 120
        return SimpleNamespace(size=(w, h), bgra=bytes(w * h * 4))


@pytest.fixture
def ocr(monkeypatch):
    import mss
    engine = _FakeOCR()
    monkeypatch.setattr(hybrid_pointer, "_get_ocr", lambda: engine)
    monkeypatch.setattr(hybrid_pointer, "_ocr_last", None)
    monkeypatch.setattr(mss, "MSS", _FakeMSS, raising=False)
    return engine


def test_an_ocr_hit_is_in_screen_pixels(ocr, monkeypatch):
    """A hit on the right-hand screen is 1920 px to the right of the same
    spot on the primary - it used to come back without that 1920."""
    monkeypatch.setattr(hybrid_pointer, "_monitor_for", lambda hwnd=None: dict(MONITORS[0]))
    maximised = SimpleNamespace(BoundingRectangle=_Rect(1920, 0, 3840, 1040))
    monkeypatch.setattr(hybrid_pointer, "_pointing_root", lambda hwnd=None: maximised)
    hit = hybrid_pointer._find_via_ocr("Captions")
    assert hit is not None and hit.source == "ocr"
    assert (hit.x, hit.y) == (1920 + 55, 86)
    assert hit.bbox == (1950, 81, 2001, 92)


def test_ocr_reads_only_the_apps_window(ocr, monkeypatch):
    """A window that is not maximised: OCR reads it, not the monitor around
    it, so a hit comes back offset by the WINDOW's position (LG-05)."""
    monkeypatch.setattr(hybrid_pointer, "_monitor_for", lambda hwnd=None: dict(MONITORS[1]))
    window = SimpleNamespace(BoundingRectangle=_Rect(100, 50, 1000, 700))
    monkeypatch.setattr(hybrid_pointer, "_pointing_root", lambda hwnd=None: window)
    hit = hybrid_pointer._find_via_ocr("Captions")
    assert (hit.x, hit.y) == (100 + 55, 50 + 86)


def test_ocr_reads_the_apps_window_never_the_taskbar(monkeypatch):
    """24 Sep: "Search" on Takealot matched "Q Search" at (566, 1056) - the
    Windows taskbar. A maximised window stops at the taskbar (LG-05)."""
    monkeypatch.setattr(hybrid_pointer, "_monitor_for", lambda hwnd=None: dict(MONITORS[1]))
    browser = SimpleNamespace(BoundingRectangle=_Rect(-8, -8, 1928, 1040))   # maximised
    monkeypatch.setattr(hybrid_pointer, "_pointing_root", lambda hwnd=None: browser)
    region = hybrid_pointer._ocr_region()
    assert region == {"left": 0, "top": 0, "width": 1920, "height": 1040}
    assert region["top"] + region["height"] <= 1040 < 1056, "the taskbar is not read"


def test_ocr_runs_one_at_a_time_and_shares_its_read(ocr):
    ocr.delay = 0.2
    monitor = dict(MONITORS[1])
    asked = time.monotonic()
    threads = [threading.Thread(target=hybrid_pointer._ocr_screen, args=(monitor, asked))
               for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert ocr.most == 1, "never two at once: they only slow each other down"
    assert ocr.calls == 1, "three asking about the same moment share one read"
    hybrid_pointer._ocr_screen(monitor, time.monotonic() + 5)
    assert ocr.calls == 2, "a later question reads the screen again"


def test_a_given_image_is_read_as_given(ocr):
    """The path the step checks use: their own image, their own pixels."""
    from PIL import Image
    hit = hybrid_pointer._find_via_ocr("Captions", pil_image=Image.new("RGB", (200, 120)))
    assert (hit.x, hit.y) == (55, 86)


# ── the UIA walk keeps its deadline ──────────────────────────────────────────

class _SlowNode:
    """A control in an app too busy to answer quickly, in a huge tree."""
    COST = 0.02

    def __getattr__(self, name):
        if name in ("Name", "ControlTypeName"):
            time.sleep(self.COST)
            return "Pane" if name == "Name" else "PaneControl"
        if name == "BoundingRectangle":
            time.sleep(self.COST)
            return _Rect(0, 0, 10, 10)
        raise AttributeError(name)

    def GetChildren(self):
        return [_SlowNode() for _ in range(5)]


def test_the_uia_walk_keeps_its_deadline(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "uiautomation", SimpleNamespace())
    monkeypatch.setattr(hybrid_pointer, "_pointing_root", lambda hwnd=None: _SlowNode())
    started = time.monotonic()
    assert hybrid_pointer._find_via_uia("Captions", max_seconds=0.3) is None
    assert time.monotonic() - started < 0.6, "checked every 32 nodes, this ran ~2 s"


# ── the manager does not wait for the local tiers to fail ────────────────────

def _companion(monkeypatch, *, local_s=3.0, local=None, vision=None, vision_s=0.1):
    from companion_manager import CompanionManager
    import companion_manager as cm

    companion = CompanionManager.__new__(CompanionManager)
    companion._privacy_guard = False
    companion._dpi_scale = lambda: 1.0
    calls = {"local": 0, "vision": 0}

    def find_target(query, **kw):
        calls["local"] += 1
        time.sleep(local_s)
        return local

    async def vision_point(query, shot=None, **kw):
        calls["vision"] += 1
        await asyncio.sleep(vision_s)
        return vision

    monkeypatch.setattr(hybrid_pointer, "find_target", find_target)
    companion._vision_point = vision_point
    monkeypatch.setattr(cm, "active_window_title", lambda: "Untitled - CapCut")
    return companion, calls


def _timed(coro):
    """How long the call itself took. Not asyncio.run's: at the end it
    waits for the losing local lookup's thread, which the app's long-lived
    loop never does."""
    async def run():
        started = time.monotonic()
        out = await coro
        return out, time.monotonic() - started
    return asyncio.run(run())


def test_a_slow_local_miss_does_not_hold_up_the_model(monkeypatch):
    seen = SimpleNamespace(x=5.0, y=6.0, label="Captions", via="vision")
    companion, calls = _companion(monkeypatch, local_s=2.0, vision=seen)
    found, took = _timed(companion._resolve_point("Captions"))
    assert found is seen
    assert took < companion.LOCAL_GRACE_S + 1.0, f"took {took:.1f}s - it waited for the local tiers"


def test_in_an_app_with_no_controls_the_model_is_asked_at_once(monkeypatch):
    seen = SimpleNamespace(x=5.0, y=6.0, label="Captions", via="vision")
    companion, calls = _companion(monkeypatch, local_s=2.0, vision=seen)
    companion._note_uia("Untitled - CapCut", 1)          # what planning found
    found, took = _timed(companion._resolve_point("Captions"))
    assert found is seen and took < 0.6, f"took {took:.1f}s"


def test_a_quick_local_hit_never_asks_the_model(monkeypatch):
    target = hybrid_pointer.Target(x=100, y=40, bbox=(90, 30, 110, 50), label="Save",
                                   source="uia", confidence=0.95)
    companion, calls = _companion(monkeypatch, local_s=0.05, local=target)
    found, _took = _timed(companion._resolve_point("Save"))
    assert (found.x, found.y, found.via) == (100, 40, "uia")
    assert calls["vision"] == 0, "free and right: no paid call"


def test_when_the_model_sees_nothing_ocr_still_gets_its_say(monkeypatch):
    target = hybrid_pointer.Target(x=100, y=40, bbox=(90, 30, 110, 50), label="Captions",
                                   source="ocr", confidence=0.9)
    companion, calls = _companion(monkeypatch, local_s=0.3, local=target, vision=None,
                                  vision_s=0.05)
    companion._note_uia("Untitled - CapCut", 1)
    found, _took = _timed(companion._resolve_point("Captions"))
    assert found is not None and found.via == "ocr"


# ── a step's arrow: looked up once, and never drawn late ─────────────────────

def _guide(monkeypatch, *, resolve_s=0.2):
    from companion_manager import CompanionManager

    companion = CompanionManager.__new__(CompanionManager)
    plan = SimpleNamespace(steps=[SimpleNamespace(target="Captions", _watcher=None),
                                  SimpleNamespace(target="Auto captions", _watcher=None)])
    companion._lesson_plan = plan
    companion._lesson_step_idx = 0
    companion._step_point = {}
    companion._guide_stats = None
    companion._activity = lambda *_a: None
    drawn, looked = [], []

    async def resolve(query, **kw):
        looked.append(query)
        await asyncio.sleep(resolve_s)
        return SimpleNamespace(x=1.0, y=2.0, label=query, via="vision", bbox=None)

    companion._resolve_point = resolve
    companion._show_guide_point = lambda found, query: drawn.append(query) or True
    return companion, plan, drawn, looked


def test_an_arrow_for_a_step_that_is_over_is_not_drawn(monkeypatch):
    companion, plan, drawn, _looked = _guide(monkeypatch)

    async def scenario():
        job = asyncio.ensure_future(companion._point_step_target(0, plan.steps[0], note=False))
        await asyncio.sleep(0.05)
        companion._lesson_step_idx = 1          # the user said "next"
        await job

    asyncio.run(scenario())
    assert drawn == [], "step one's arrow must not land on step two"


def test_step_one_is_looked_up_once_however_many_ask(monkeypatch):
    companion, plan, drawn, looked = _guide(monkeypatch)

    async def scenario():
        await asyncio.gather(companion._point_step_target(0, plan.steps[0], note=False),
                             companion._point_step_target(0, plan.steps[0]))

    asyncio.run(scenario())
    assert looked == ["Captions"], "planned and said: one lookup, not two"
    assert drawn == ["Captions", "Captions"]


def test_a_new_step_stops_the_last_steps_lookup(monkeypatch):
    companion, plan, drawn, _looked = _guide(monkeypatch, resolve_s=5.0)

    async def scenario():
        job = asyncio.ensure_future(companion._point_step_target(0, plan.steps[0]))
        await asyncio.sleep(0.05)
        companion._lesson_step_idx = 1
        companion._drop_step_jobs(keep=1)       # what presenting step two does
        started = time.monotonic()
        await job                               # returns quietly, and at once
        return time.monotonic() - started

    assert asyncio.run(scenario()) < 1.0
    assert drawn == []


# ── "@element" anchors resolve again ─────────────────────────────────────────

def test_anchors_resolve_again(monkeypatch):
    from companion_manager import CompanionManager

    def find_target(query, *, skip_uia=False, skip_ocr=False, **kw):
        return hybrid_pointer.Target(x=50, y=20, bbox=(40, 10, 60, 30), label=query,
                                     source="uia", confidence=0.95)

    monkeypatch.setattr(hybrid_pointer, "find_target", find_target)
    companion = CompanionManager.__new__(CompanionManager)
    companion._shot = lambda idx=1: None
    assert companion._resolve_anchor("Save") == (40.0, 10.0, 60.0, 30.0)
