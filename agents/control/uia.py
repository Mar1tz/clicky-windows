"""Driving a window through UI Automation - without focus, without the cursor.

A click is the crudest way to press a button. Windows already exposes
every button, checkbox, tab and text box through UI Automation, and a
control's own `Invoke` pattern presses it where it stands: the window can
be behind other windows or minimised, the mouse stays where the user left
it, and nothing depends on a screenshot being read correctly.

So this is the tier between "the app has an API" and "take the user's
mouse". It covers ordinary Win32, WinForms, WPF and UWP apps well.

What it does NOT cover, and the reason the ladder has a rung below it:
Chromium and Electron apps (VS Code, Spotify, Slack, Discord, Teams)
ignore posted input and expose a partial tree, so for those the adapters
reach for the app's CLI or a deep link first - which is exactly why those
adapters exist.

Tora's own windows are invisible here, as everywhere else: an agent that
can press its own approval dialog is a loop waiting to happen.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any, Optional

log = logging.getLogger("agents.control.uia")

SEARCH_DEPTH = 12
MAX_NODES = 2500

# What we can ask a control to do, and the UIA pattern behind each.
ACTIONS = ("invoke", "toggle", "select", "expand", "collapse", "set_value", "read")


def _auto():
    try:
        import uiautomation as auto
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError("UI Automation needs the `uiautomation` package") from exc
    return auto


def _window_control(window: str):
    """The UIA element for a window title fragment - never one of ours."""
    from agents.tools.app_tools import is_own_window, list_windows
    auto = _auto()
    frag = (window or "").strip().lower()
    for w in list_windows():
        if frag and frag not in w["title"].lower():
            continue
        if is_own_window(w["hwnd"], w["title"]):
            continue
        ctrl = auto.ControlFromHandle(w["hwnd"])
        if ctrl is not None:
            return ctrl, w
    titles = ", ".join(w["title"][:40] for w in list_windows()[:8]) or "nothing"
    raise LookupError(f"no open window matches {window!r}. Open: {titles}")


def _walk(root, limit: int = MAX_NODES, depth: int = SEARCH_DEPTH):
    """Breadth-first over the control tree, bounded in both directions so a
    browser page full of text nodes cannot hang the call."""
    queue = deque([(root, 0)])
    seen = 0
    while queue and seen < limit:
        node, d = queue.popleft()
        seen += 1
        yield node
        if d >= depth:
            continue
        try:
            children = node.GetChildren()
        except Exception:  # noqa: BLE001 - a control can vanish mid-walk
            continue
        queue.extend((c, d + 1) for c in children)


# UIA pattern ids, fetched the portable way: a control class only defines
# the getters for patterns it might have, but GetPattern works on any of
# them and returns None when the pattern is not supported.
def _pattern(node, which: str):
    auto = _auto()
    ids = {"invoke": auto.PatternId.InvokePattern, "toggle": auto.PatternId.TogglePattern,
           "select": auto.PatternId.SelectionItemPattern,
           "expand": auto.PatternId.ExpandCollapsePattern, "collapse": auto.PatternId.ExpandCollapsePattern,
           "set_value": auto.PatternId.ValuePattern, "read": auto.PatternId.ValuePattern,
           "legacy": auto.PatternId.LegacyIAccessiblePattern}
    try:
        return node.GetPattern(ids[which])
    except Exception:  # noqa: BLE001 - a control can vanish or refuse mid-walk
        return None


def _patterns(node) -> list[str]:
    out = [name for name in ("invoke", "toggle", "select", "expand", "set_value")
           if _pattern(node, name) is not None]
    if "invoke" not in out and _can_default(node):
        out.insert(0, "invoke")           # legacy DoDefaultAction: how Electron answers
    return out


def _can_default(node) -> bool:
    legacy = _pattern(node, "legacy")
    if legacy is None:
        return False
    try:
        return bool(legacy.DefaultAction)
    except Exception:  # noqa: BLE001
        return False


def _name(node) -> str:
    try:
        return (node.Name or "").strip()
    except Exception:  # noqa: BLE001
        return ""


class _Seen:
    """A named control as one read of the tree saw it - and, only when it is
    wanted, the live control behind it, to act on."""

    def __init__(self, name: str, kind: str, *, element=None, control=None,
                 enabled: Optional[bool] = None, actions: Optional[list] = None):
        self.name, self.kind = name, kind              # kind: "ButtonControl"
        self._element, self._control = element, control
        self._enabled, self._actions = enabled, actions

    @property
    def control(self):
        if self._control is None:
            self._control = _auto().Control.CreateControlFromElement(self._element)
        return self._control

    @property
    def actions(self) -> list[str]:
        if self._actions is None:
            self._actions = _patterns(self.control)
        return self._actions

    @property
    def enabled(self) -> bool:
        if self._enabled is None:
            self._enabled = bool(self.control.IsEnabled)
        return self._enabled


# Read with every control in the one call, so choosing one costs nothing
# more: what it is, and what it can be asked to do (UIA property ids).
_CAN = (("invoke", 30031), ("toggle", 30041), ("select", 30036),
        ("expand", 30028), ("set_value", 30043))
_LEGACY, _DEFAULT_ACTION, _ENABLED = 30090, 30100, 30010
_NAMED = None


def _named(root, *, abilities: bool = True):
    """Every named control in `root`, top first - read in ONE cross-process
    call (SC-07, 18 Oct 2026): Windows builds the tree in the app's own
    process with each control's name, type and abilities, and walking it is
    then in memory. The walk below asked every node for each of those in
    turn, a COM round trip apiece, and stopped at depth 12 - short of an
    Electron app's controls. An app that will not build the cache is walked.

    Without `abilities` only names and types are read - a search, which
    asks the few controls it matches what they can do."""
    auto = _auto()
    try:
        from uiautomation import uiautomation as _ua
        global _NAMED
        ui = _ua._AutomationClient.instance().IUIAutomation
        P = auto.PropertyId
        if _NAMED is None:
            _NAMED = ui.CreateAndCondition(
                ui.ControlViewCondition, ui.CreateNotCondition(ui.CreatePropertyCondition(P.NameProperty, "")))
        request = ui.CreateCacheRequest()
        extra = (_ENABLED, _LEGACY, _DEFAULT_ACTION, *(pid for _a, pid in _CAN)) if abilities else ()
        for prop in (P.NameProperty, P.ControlTypeProperty, *extra):
            request.AddProperty(prop)
        request.TreeScope = 7                                   # TreeScope_Subtree
        request.TreeFilter = _NAMED
        top = root.Element.BuildUpdatedCache(request)
    except Exception:  # noqa: BLE001 - an app that will not build the cache: walk it
        log.debug("cached control read failed - walking", exc_info=True)
        return (_Seen(_name(n), _kind(n), control=n) for n in _walk(root) if _name(n))
    return _read(top, auto.ControlTypeNames, abilities)


def _read(top, kinds: dict, abilities: bool):
    queue = deque([top])
    while queue:
        el = queue.popleft()
        name = (el.CachedName or "").strip()
        if name and not abilities:
            yield _Seen(name, kinds.get(el.CachedControlType, ""), element=el)
        elif name:
            value = el.GetCachedPropertyValue
            can = [a for a, pid in _CAN if value(pid)]
            if "invoke" not in can and value(_LEGACY) and value(_DEFAULT_ACTION):
                can.insert(0, "invoke")           # legacy DoDefaultAction: how Electron answers
            yield _Seen(name, kinds.get(el.CachedControlType, ""), element=el,
                        enabled=bool(value(_ENABLED)), actions=can)
        kids = el.GetCachedChildren()
        if kids:
            queue.extend(kids.GetElement(i) for i in range(kids.Length))


def _kind(node) -> str:
    try:
        return node.ControlTypeName or ""
    except Exception:  # noqa: BLE001
        return ""


def controls(window: str, *, limit: int = 40, query: str = "") -> list[dict[str, Any]]:
    """Named, actionable controls in a window - what the agent may ask for."""
    root, info = _window_control(window)
    q = query.strip().lower()
    out: list[dict[str, Any]] = []
    for seen in _named(root):
        if q and q not in seen.name.lower():
            continue
        try:
            actions = seen.actions
            if not actions or not seen.kind:
                continue
            enabled = seen.enabled
        except Exception:  # noqa: BLE001 - a control can vanish mid-read
            continue
        row = {"name": seen.name, "type": seen.kind.replace("Control", ""),
               "actions": actions, "enabled": enabled}
        if row not in out:
            out.append(row)
        if len(out) >= limit:
            break
    return out


def _find(root, name: str, *, kind: str = ""):
    """Best control for a name: exact, then prefix, then contains - and a
    control that can act beats one that only shows text."""
    want, kind = name.strip().lower(), kind.strip().lower()
    exact, prefix, contains = [], [], []
    for seen in _named(root, abilities=False):
        got = seen.name.lower()
        if kind and kind not in seen.kind.lower():
            continue
        if got == want:
            exact.append(seen)
        elif got.startswith(want):
            prefix.append(seen)
        elif want in got:
            contains.append(seen)
        if len(exact) > 2:
            break
    for pool in (exact, prefix, contains):
        for seen in pool:
            try:
                if seen.actions:
                    return seen.control
            except Exception:  # noqa: BLE001 - a control can vanish mid-read
                continue
        if pool:
            return pool[0].control
    return None


def act(window: str, control: str, action: str = "invoke", *, value: str = "",
        kind: str = "") -> str:
    """Do `action` to a named control in `window`. Nothing is focused and
    the cursor does not move."""
    action = (action or "invoke").lower()
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    root, info = _window_control(window)
    node = _find(root, control, kind=kind)
    if node is None:
        names = ", ".join(c["name"] for c in controls(window, limit=20)) or "(none named)"
        raise LookupError(f"no control called {control!r} in {info['title']!r}. I can see: {names}")
    label = f"{_name(node)!r} in {info['title']!r}"
    try:
        if action == "read":
            value = _pattern(node, "read")
            text = ""
            if value is not None:
                try:
                    text = value.Value
                except Exception:  # noqa: BLE001
                    text = ""
            return f"{label}: {text or _name(node)}"
        if action == "invoke":
            pattern = _pattern(node, "invoke")
            if pattern is not None:
                pattern.Invoke()
            else:
                select = _pattern(node, "select")
                if select is not None:
                    select.Select()                  # menu items and list rows
                else:
                    legacy = _pattern(node, "legacy")
                    if legacy is None:
                        raise RuntimeError("it cannot be pressed from the background")
                    legacy.DoDefaultAction()
            return f"pressed {label}"
        if action == "toggle":
            _require(node, "toggle", label).Toggle()
            return f"toggled {label}"
        if action == "select":
            _require(node, "select", label).Select()
            return f"selected {label}"
        if action == "expand":
            _require(node, "expand", label).Expand()
            return f"expanded {label}"
        if action == "collapse":
            _require(node, "collapse", label).Collapse()
            return f"collapsed {label}"
        _require(node, "set_value", label).SetValue(value)
        return f"set {label} to {value!r}"
    except LookupError:
        raise
    except Exception as exc:  # noqa: BLE001 - UIA raises COM errors of every shape
        raise RuntimeError(f"{label} would not {action}: {type(exc).__name__}: {exc}") from exc


def _require(node, which: str, label: str):
    pattern = _pattern(node, which)
    if pattern is None:
        raise RuntimeError(f"{label} does not support {which} from the background")
    return pattern
