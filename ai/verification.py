"""
Did the user actually get there? — the guide's verification engine.

A step is not "click the Save button". A step is **an outcome**: the document
is saved. The user might click Save, press Ctrl+S, or use the File menu — all
three succeed, and an engine that only watches for the click punishes the two
faster routes and then sits there waiting.

So every step declares an Expectation: several independent, optional signals
that each mean the outcome was reached. Verification is OR across them — the
first satisfied signal wins — which is what makes alternative paths work
without the planner having to enumerate every possible click.

The layers, cheapest first (the expensive one only runs if the rest are
unsure):

  1. Process / window   is the right app now in front, or running at all?
                        A few hundred microseconds, and the most robust
                        signal there is: it does not care HOW it got there.
  2. UI Automation      did a control appear or disappear, did a field take
                        focus? Milliseconds.
  3. Browser URL        did the address bar navigate?
  4. Vision             only when the layers above cannot tell.

Everything returns a CONFIDENCE, not a yes/no, because the right behaviour
differs: near-certain should advance silently, middling should advance but
keep watching, and low should look again rather than nag.

StateObserver on top of that answers a different question — "is anything
happening at all?" — which is how being stuck is told apart from merely being
slow.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Optional

from tutor import active_process_name, active_window_title

_log = logging.getLogger("tora.verify")

# Confidence each kind of signal carries when it is satisfied. Process and
# window state are the most trustworthy because they describe the OUTCOME
# rather than the gesture that produced it.
WEIGHT = {
    "process": 0.95,
    "window_title": 0.95,
    "url_contains": 0.95,
    "control_appears": 0.90,
    "control_gone": 0.85,
    "focused": 0.85,
    "visual": 0.80,
}

# What the runner does with a confidence.
ADVANCE = 0.85        # sure enough to move on
UNCERTAIN = 0.40      # below this, treat as "not done"


@dataclass
class Expectation:
    """The observable outcome of a step — any one of these means success."""
    process: str = ""           # e.g. "explorer.exe" — running / frontmost
    window_title: str = ""      # substring of the resulting window title
    control_appears: str = ""
    control_gone: str = ""
    focused: str = ""
    url_contains: str = ""
    visual: str = ""            # natural language, checked by the vision model

    def signals(self) -> list[tuple[str, str]]:
        return [
            (k, v) for k, v in (
                ("process", self.process),
                ("window_title", self.window_title),
                ("url_contains", self.url_contains),
                ("control_appears", self.control_appears),
                ("control_gone", self.control_gone),
                ("focused", self.focused),
                ("visual", self.visual),
            ) if v
        ]

    def __bool__(self) -> bool:
        return bool(self.signals())

    @property
    def has_local_signal(self) -> bool:
        """True if anything can be checked without the vision model."""
        return any(k != "visual" for k, _ in self.signals())


@dataclass
class Verdict:
    confidence: float = 0.0
    method: str = ""            # which signal decided it
    detail: str = ""
    needs_vision: bool = False  # only a visual signal is left to try

    @property
    def done(self) -> bool:
        return self.confidence >= ADVANCE

    @property
    def unsure(self) -> bool:
        return UNCERTAIN <= self.confidence < ADVANCE


# ── Layer 1: process / window ────────────────────────────────────────────────

def process_running(exe_name: str) -> bool:
    """Is a process with this executable name running? (Toolhelp32 snapshot.)

    Used for outcomes like "Chrome is open" that hold whether or not the
    window happens to be in front right now.
    """
    name = (exe_name or "").strip().lower()
    if not name:
        return False
    if not name.endswith(".exe"):
        name += ".exe"
    TH32CS_SNAPPROCESS = 0x00000002
    MAX_PATH = 260

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * MAX_PATH),
        ]

    try:
        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == wintypes.HANDLE(-1).value or not snap:
            return False
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if not k32.Process32First(snap, ctypes.byref(entry)):
                return False
            while True:
                try:
                    exe = entry.szExeFile.decode("latin-1", "ignore").lower()
                except Exception:
                    exe = ""
                if exe == name:
                    return True
                if not k32.Process32Next(snap, ctypes.byref(entry)):
                    return False
        finally:
            k32.CloseHandle(snap)
    except Exception:
        _log.debug("process scan failed", exc_info=True)
        return False


def _process_signal(value: str) -> tuple[float, str]:
    """How strongly the named app's state says the step was performed.

    Frontmost is real evidence. Merely RUNNING is not, and treating it as
    proof was the single worst bug in the guide: a walkthrough inside Chrome
    has `process: chrome.exe` on most of its steps, chrome.exe is running
    before the user lifts a finger, and so every one of those steps looked
    finished the instant it was spoken. It stays as a weak contributor —
    below ADVANCE, so it can never finish a step on its own.
    """
    want = (value or "").strip().lower()
    if not want:
        return 0.0, ""
    if not want.endswith(".exe"):
        want += ".exe"
    front = (active_process_name() or "").lower()
    if front == want:
        return WEIGHT["process"], f"{want} is the active window"
    if process_running(want):
        return 0.5, f"{want} is running (but not in front)"
    return 0.0, ""


# ── The engine ───────────────────────────────────────────────────────────────

def check_signal(kind: str, value: str) -> tuple[float, str]:
    """One signal's confidence right now, and how it reads. Blocking."""
    from ai import guide_state
    from ai.gemini_planner import StepCondition

    try:
        if kind == "process":
            return _process_signal(value)
        if kind == "window_title":
            ok = value.lower() in (active_window_title() or "").lower()
            return (WEIGHT["window_title"] if ok else 0.0,
                    f'window title contains "{value}"')
        # The remaining kinds are exactly guide_state's vocabulary.
        ok = guide_state.check(StepCondition(kind=kind, value=value)) is True
        return (WEIGHT.get(kind, 0.8) if ok else 0.0, f"{kind}: {value}")
    except Exception:
        _log.debug("signal %s failed", kind, exc_info=True)
        return 0.0, ""


def verify_local(expectation: Expectation) -> Verdict:
    """Check every non-vision signal and keep the strongest. Blocking.

    OR semantics: any signal can carry the step, so the user reaches the
    outcome their own way. It scans them all rather than returning on the
    first hit, because a weak signal (an app that is merely running) must not
    hide a strong one behind it.
    """
    if not expectation:
        return Verdict(confidence=0.0, method="none", detail="nothing to check")

    best = Verdict(confidence=0.0, method="none", detail="no signal satisfied")
    for kind, value in expectation.signals():
        if kind == "visual":
            continue
        conf, detail = check_signal(kind, value)
        if conf > best.confidence:
            best = Verdict(confidence=conf, method=kind, detail=detail)
        if best.confidence >= ADVANCE:
            return best

    best.needs_vision = bool(expectation.visual) and not best.done
    return best


def satisfied_now(expectation: Expectation) -> set[str]:
    """The signals that are ALREADY true, before the user has done anything.

    A signal that is true at step start cannot tell "the user did it" from
    "it already looked like that", so the guide mutes exactly those and keeps
    the rest. Muting the whole expectation instead — which is what the guide
    used to do — threw away a free, instant answer on four steps out of five.
    """
    out: set[str] = set()
    for kind, value in expectation.signals():
        if kind == "visual":
            continue
        conf, _detail = check_signal(kind, value)
        if conf > 0.0:
            out.add(kind)
    return out


class ArmedExpectation:
    """A step's expectation with the already-true signals muted.

    Call arm() once when the step is shown; call the instance from then on,
    exactly like verify_local. Muted signals are re-armed the moment they go
    false, because that is a real transition the user can then complete.
    """

    def __init__(self, expectation: Expectation) -> None:
        self.expectation = expectation
        self.muted: set[str] = set()

    def arm(self) -> list[str]:
        """Mute what is already true. Returns the signals still worth watching."""
        self.muted = satisfied_now(self.expectation)
        return [k for k, _ in self.expectation.signals()
                if k not in self.muted and k != "visual"]

    @property
    def armed_any(self) -> bool:
        return any(k not in self.muted
                   for k, _ in self.expectation.signals() if k != "visual")

    def __call__(self) -> Verdict:
        best = Verdict(confidence=0.0, method="none", detail="no signal satisfied")
        for kind, value in self.expectation.signals():
            if kind == "visual":
                continue
            conf, detail = check_signal(kind, value)
            if kind in self.muted:
                # Still watching it: once it goes false the user can make it
                # true again, and that transition is exactly what we want.
                if conf == 0.0:
                    self.muted.discard(kind)
                continue
            if conf > best.confidence:
                best = Verdict(confidence=conf, method=kind, detail=detail)
            if best.confidence >= ADVANCE:
                return best
        best.needs_vision = bool(self.expectation.visual) and not best.done
        return best


# ── Is anything happening at all? ────────────────────────────────────────────

@dataclass
class StateObserver:
    """Tracks whether the screen is changing, to tell stuck from merely slow.

    A step that is not progressing looks identical to one the user is taking
    their time over — unless you notice that nothing on screen has moved. All
    three probes are cheap enough to run on the guide's poll loop.
    """
    _last: tuple = field(default=("", "", ""))
    _changed_at: float = field(default_factory=time.monotonic)

    def snapshot(self) -> tuple:
        proc = (active_process_name() or "").lower()
        title = (active_window_title() or "")[:120]
        focused = ""
        try:
            from ai.hybrid_pointer import focused_element
            f = focused_element()
            if f:
                focused = (f[1] or "")[:60]
        except Exception:
            pass
        return (proc, title, focused)

    def poll(self) -> bool:
        """Sample now. True if anything changed since the last sample."""
        now = self.snapshot()
        if now != self._last:
            self._last = now
            self._changed_at = time.monotonic()
            return True
        return False

    def idle_seconds(self) -> float:
        return time.monotonic() - self._changed_at

    def reset(self) -> None:
        self._last = self.snapshot()
        self._changed_at = time.monotonic()
