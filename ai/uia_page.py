"""
The window in front, through Windows UI Automation: its controls by name
and its page's text - read, never photographed.

What typing for the user and answering an email both stand on (asked for
on 10 Oct 2026: "it mustn't take screenshots and click - it needs to be
instant"). A browser page is in the accessibility tree, so its text, its
buttons and its boxes are a few milliseconds away by name. Windows' own
search (FindAll with a condition) does it; walking a page of thousands of
nodes from Python takes seconds. A button is pressed through its Invoke
pattern: no mouse moves and nothing is clicked.
"""

from __future__ import annotations

import contextlib
import ctypes
import time
from typing import Callable, Optional

MAX_TEXT = 30000           # characters of a page: a long thread, not a whole mailbox


@contextlib.contextmanager
def in_thread():
    """UI Automation made ready on this thread - a worker thread (asyncio's
    to_thread) has no COM until it is asked for."""
    try:
        import uiautomation as auto
        ready = auto.UIAutomationInitializerInThread()
    except Exception:  # noqa: BLE001 - no UI Automation here (tests): nothing to ready
        ready = contextlib.nullcontext()
    with ready:
        yield


def _ui():
    from uiautomation import uiautomation as _ua
    return _ua._AutomationClient.instance().IUIAutomation


def window():
    """The window in front."""
    import uiautomation as auto
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return auto.ControlFromHandle(hwnd) if hwnd else None


def find_all(root, *, name: Optional[str] = None, control_type: Optional[int] = None, limit: int = 40) -> list:
    """The controls under `root` with this name and/or type."""
    import uiautomation as auto
    if root is None:
        return []
    ui = _ui()
    conds = []
    if name is not None:
        conds.append(ui.CreatePropertyCondition(auto.PropertyId.NameProperty, name))
    if control_type is not None:
        conds.append(ui.CreatePropertyCondition(auto.PropertyId.ControlTypeProperty, control_type))
    cond = (conds[0] if len(conds) == 1 else ui.CreateAndCondition(conds[0], conds[1]) if conds
            else ui.CreateTrueCondition())
    found = root.Element.FindAll(4, cond)                     # TreeScope_Descendants
    n = found.Length if found else 0
    return [auto.Control.CreateControlFromElement(found.GetElement(i)) for i in range(min(n, limit))]


def page_text(win) -> str:
    """The text of the web page (or document) in `win`; "" when it shows none."""
    import uiautomation as auto
    for doc in find_all(win, control_type=auto.ControlType.DocumentControl, limit=4):
        try:
            text = doc.GetTextPattern().DocumentRange.GetText(MAX_TEXT * 2)
        except Exception:  # noqa: BLE001 - no text pattern on this one
            continue
        if text and len(text.strip()) > 200:
            return tidy(text)[:MAX_TEXT]
    return ""


def tidy(text: str) -> str:
    """Text with its blank runs closed up."""
    import re
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text or "")).strip()


def value(control) -> str:
    try:
        return str(control.GetValuePattern().Value or "")
    except Exception:  # noqa: BLE001
        return ""


def shown(control) -> bool:
    try:
        return not control.IsOffscreen
    except Exception:  # noqa: BLE001
        return True


def focus(control) -> None:
    control.SetFocus()
    time.sleep(0.05)


def invoke(control) -> None:
    """Press a button through Windows - the mouse never moves."""
    control.GetInvokePattern().Invoke()


def wait(found: Callable, seconds: float):
    deadline = time.monotonic() + seconds
    while True:
        got = found()
        if got is not None or time.monotonic() > deadline:
            return got
        time.sleep(0.05)
