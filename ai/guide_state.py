"""
Is a guide step done yet? — read the screen's state, check a condition.

A step's completion condition (ai.gemini_planner.StepCondition) says what
observable means it worked: a window title changed, a browser navigated, a
field got focus, a panel appeared. This module reads those observables off
live Windows state and answers yes / no / can't-tell for a condition.

Everything here is local and cheap — window title, the UIA focused element,
the control-name snapshot, the browser address bar. No pixels, nothing sent
anywhere. It is what lets the guide advance on its own (M4) without asking
the user "are you there yet?", and what lets M3 tell the user "that doesn't
look done" when they say next too early.

read_state() does the one expensive thing (a bounded UIA walk) once; evaluate
is then pure. Both are blocking — call read_state in a thread.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from tutor import active_window_title

_log = logging.getLogger("tora.guide")


@dataclass
class GuideState:
    title: str = ""
    focused_name: str = ""
    focused_type: str = ""            # e.g. "edit", "button" (suffix stripped)
    control_names: list = field(default_factory=list)   # lowercased
    url: str = ""

    def has_control(self, name: str) -> bool:
        n = name.lower()
        return any(n in c or c in n for c in self.control_names)


def _read_url() -> str:
    """Best-effort read of the browser address bar via UIA ValuePattern.

    Chromium and Firefox expose the address bar as an Edit control named
    something like "Address and search bar". Reading its value tells us which
    page is open, which is the most reliable "did the browser navigate" signal
    there is. Silent and best-effort — any failure just yields "".
    """
    try:
        import uiautomation as auto
    except ImportError:
        return ""
    try:
        root = auto.GetForegroundControl() or auto.GetRootControl()
    except Exception:
        return ""
    if root is None:
        return ""

    queue = [(root, 0)]
    visited = 0
    while queue and visited < 900:
        node, depth = queue.pop(0)
        visited += 1
        try:
            ct = node.ControlTypeName or ""
            name = (node.Name or "").lower()
        except Exception:
            continue
        if "Edit" in ct and ("address" in name or "search" in name or "url" in name):
            try:
                val = node.GetValuePattern().Value
                if val:
                    return str(val)
            except Exception:
                pass
        if depth < 12:
            try:
                queue.extend((c, depth + 1) for c in node.GetChildren())
            except Exception:
                continue
    return ""


def read_state(*, want_url: bool = False) -> GuideState:
    """Snapshot the current screen state. Blocking — call in a thread.

    want_url is off by default because the address-bar walk is a second UIA
    pass; only the url_contains condition needs it, so the runner asks for it
    only when a step actually uses it.
    """
    title = active_window_title()

    focused_name = focused_type = ""
    try:
        from ai.hybrid_pointer import focused_element
        focused = focused_element()
        if focused:
            ftype, fname, is_pw = focused
            if not is_pw:                          # never read a password field
                focused_name = (fname or "").lower()
                focused_type = (ftype or "").replace("Control", "").lower()
    except Exception:
        _log.debug("focus read failed", exc_info=True)

    control_names: list = []
    try:
        from ai.hybrid_pointer import list_pointables
        control_names = [p[2].lower() for p in list_pointables(limit=60)]
    except Exception:
        _log.debug("control snapshot failed", exc_info=True)

    url = _read_url() if want_url else ""
    return GuideState(
        title=title, focused_name=focused_name, focused_type=focused_type,
        control_names=control_names, url=url,
    )


def control_exists(
    name: str, *, max_nodes: int = 3000, max_depth: int = 30,
    max_seconds: float = 2.5,
) -> bool:
    """Is a control named `name` present in the foreground window right now?

    Early-exits on the first match, so a control that IS there is found in a
    few milliseconds. An ABSENT one pays the whole (bounded) walk, which is
    why callers throttle this — in a guide the control is absent for as long
    as the step is outstanding, so it would otherwise run on every poll. This
    replaced checking against the 60-item list_pointables slice, which was the
    reason auto-advance so often never fired: if the control a step waited for
    happened to fall outside that truncated slice, the condition could never
    become true and the guide sat there forever.
    """
    target = (name or "").strip().lower()
    if not target:
        return False
    try:
        import uiautomation as auto
    except ImportError:
        return False
    try:
        root = auto.GetForegroundControl() or auto.GetRootControl()
    except Exception:
        return False
    if root is None:
        return False

    # A TIME budget as well as a node one. Tree shape varies wildly — an
    # Electron window needs depth ~30 to reach its menu bar, while a shallow
    # node cap just burns the budget on nesting and misses it. The deadline
    # bounds the worst case (an absent control) no matter the shape.
    deadline = time.monotonic() + max_seconds
    queue = [(root, 0)]
    visited = 0
    while queue and visited < max_nodes:
        if visited % 64 == 0 and time.monotonic() > deadline:
            break
        node, depth = queue.pop(0)
        visited += 1
        try:
            nm = (node.Name or "").strip().lower()
            rect = node.BoundingRectangle
        except Exception:
            continue
        if nm and rect and rect.width() > 0 and rect.height() > 0:
            # ONLY "the wanted name appears in this control's name". The
            # reverse direction (nm in target) matched any control whose name
            # happened to be a substring of the wanted text — a control named
            # "a" or "not" matched almost anything, so a step could report
            # itself done before the user had touched it.
            if target in nm:
                return True
        if depth < max_depth:
            try:
                queue.extend((c, depth + 1) for c in node.GetChildren())
            except Exception:
                continue
    return False


def check(condition):
    """Evaluate one condition, reading ONLY what that condition needs.

    The guide polls this several times a second, so cost matters: a
    window_title check is a single Win32 call, a focused check is one UIA
    lookup, and only control_appears/gone walks the tree (with an early exit).
    read_state() below reads everything at once and is kept for the look-ahead,
    which checks several conditions against one snapshot.

    True / False, or None when it cannot be decided (visual / none).
    """
    kind = getattr(condition, "kind", "none")
    value = (getattr(condition, "value", "") or "").lower().strip()
    if not value or kind in ("none", "visual"):
        return None

    if kind == "window_title":
        return value in (active_window_title() or "").lower()

    if kind == "focused":
        try:
            from ai.hybrid_pointer import focused_element
            f = focused_element()
        except Exception:
            return False
        if not f:
            return False
        ftype, fname, is_password = f
        if is_password:
            return False
        ok = value in (fname or "").lower()
        want = (getattr(condition, "control_type", "") or "").lower()
        if want:
            return ok and want in (ftype or "").replace("Control", "").lower()
        return ok

    if kind == "control_appears":
        return control_exists(value)

    if kind == "control_gone":
        return not control_exists(value)

    if kind == "url_contains":
        return value in (_read_url() or "").lower()

    return None


def needs_url(condition) -> bool:
    return getattr(condition, "kind", "") == "url_contains"


def evaluate(condition, state: GuideState):
    """Is `condition` satisfied by `state`?

    Returns True or False for a checkable condition, and None when it cannot
    be decided from state alone (a 'visual' or 'none' condition). Callers
    treat None as "unknown" — M3 lets the user advance, M4 falls back to a
    vision check.
    """
    kind = getattr(condition, "kind", "none")
    value = (getattr(condition, "value", "") or "").lower().strip()
    if not value or kind in ("none", "visual"):
        return None

    if kind == "window_title":
        return value in (state.title or "").lower()

    if kind == "url_contains":
        return value in (state.url or "").lower()

    if kind == "focused":
        name_ok = value in (state.focused_name or "")
        want_type = (getattr(condition, "control_type", "") or "").lower()
        if want_type:
            return name_ok and want_type in (state.focused_type or "")
        return name_ok

    if kind == "control_appears":
        return state.has_control(value)

    if kind == "control_gone":
        return not state.has_control(value)

    return None
