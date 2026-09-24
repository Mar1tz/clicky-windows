"""Every named control in every open window, as an action. Any PC, no setup.

This is the milestone that makes "do anything on my computer" true on a
machine nobody has configured. A hand-written adapter covers one app; there
are eight of them, and they are the only apps with real verbs. On anyone
else's PC — different software, different language — those eight are mostly
absent and Tora can do nothing but open things.

Windows already knows better. Every Win32, WinForms, WPF and WinUI app
publishes its controls through UI Automation, each one saying what can be
done to it: a button that can be `Invoke`d, a checkbox that can be
`Toggle`d, a list row that can be `Select`ed, a field whose `Value` can be
set. Measured on this machine: **127 addressable controls in one File
Explorer window, in 0.7 seconds**, none of which anyone wrote down.

So an action here is *discovered*, not declared:

    Details        (RadioButton, Select)  ->  explorer.select_details
    Bookmark this tab (Button, Invoke)    ->  brave.click_bookmark_this_tab
    Address bar    (Edit, Value)          ->  brave.set_address_and_search_bar

Three things make this safe enough to ship.

**Apps are identified by executable, not by title.** "Google - Brave" and
"Inbox - Gmail - Brave" are one app. Window titles are also localised and
change every minute; `brave.exe` does not, which is what makes this work
identically on a German or Japanese PC.

**Nothing is enumerated until it is named.** Walking one window is half a
second; doing it for every window on every turn would cost more than the
conversation (M28). `actions()` runs only for an app the user has just
asked about, and its answer is cached.

**Every synthesised action asks, and the dangerous ones ask harder.** An
adapter author judges each action's risk by hand. Nobody judged these, so
the floor is `CHANGES` — which always confirms — and a name that looks
destructive is promoted to `SENSITIVE`, which cannot be approved by voice
or by a standing grant. Getting this wrong means an agent that can press
*Delete account* because it was a button with a name.

What this cannot reach is web page content: Chromium exposes its own
chrome and almost nothing of the page (measured: 26 nodes, 3 addressable,
for a full Gmail window, stable over three passes). That is UC-10's
problem, not this one.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import time
from ctypes import wintypes
from typing import Any, Optional

from .. import uia
from ..model import Action, Param, Refused, Risk, Tier
from ..provider import AppProfile

log = logging.getLogger("agents.control")

# How many controls a single app may contribute. A window with four hundred
# list rows is a data set, not a menu, and handing all of it to the model
# would be unreadable as well as expensive.
MAX_ACTIONS = 60

# Names that mean "someone else sees this" or "this does not come back".
# Matched as whole words against the control's name, so "Send" is caught and
# "Sender" is not. Deliberately blunt: a false SENSITIVE costs one extra
# confirmation, a false CHANGES costs the user something real.
_SENSITIVE_WORDS = {
    "delete", "remove", "erase", "destroy", "wipe", "format", "uninstall",
    "send", "post", "publish", "share", "submit", "pay", "purchase", "buy",
    "checkout", "order", "transfer", "withdraw", "confirm", "sign out",
    "log out", "logout", "reset", "revoke", "deactivate", "close account",
    "empty", "discard", "unsubscribe", "leave", "block", "report",
}

# Controls that only ever read. Safe to offer without a confirmation.
_READ_ONLY_TYPES = {"Text", "StatusBar"}

_WORD = re.compile(r"[a-z0-9]+")


def _slug(text: str) -> str:
    return "_".join(_WORD.findall(str(text).lower()))[:48]


def risk_for(name: str, control_type: str, action: str) -> Risk:
    """How much a person would mind if this happened by mistake.

    Nobody hand-judged these actions, so the rule has to be conservative:
    reading is free, everything else confirms, and anything whose name
    sounds final confirms with a typed word.
    """
    if action == "read" or control_type in _READ_ONLY_TYPES:
        return Risk.PASSIVE
    words = set(_WORD.findall(str(name).lower()))
    text = " ".join(_WORD.findall(str(name).lower()))
    for phrase in _SENSITIVE_WORDS:
        if " " in phrase:
            if phrase in text:
                return Risk.SENSITIVE
        elif phrase in words:
            return Risk.SENSITIVE
    return Risk.CHANGES


# -- which app owns a window -----------------------------------------------------

def _process_name(hwnd: int) -> str:
    """The executable behind a window: "brave.exe".

    Titles are localised and change constantly; an executable name does
    not, which is the whole reason this works the same on a PC in another
    language.
    """
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        if not pid.value:
            return ""
        # PROCESS_QUERY_LIMITED_INFORMATION: works without elevation.
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(
                    wintypes.HANDLE(handle), 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
    except Exception:  # noqa: BLE001 - a window we cannot identify is skipped
        log.debug("could not read the process for window %s", hwnd, exc_info=True)
    return ""


# Processes that host somebody else's window. A packaged app's frame
# belongs to ApplicationFrameHost, so its executable says nothing about
# which app it is - and dropping those windows made every Store app
# invisible: Calculator, Mail, Camera, Photos, Clock.
_HOST_PROCESSES = {"applicationframehost", "runtimebroker", "shellexperiencehost"}


def _title_tail(title: str) -> str:
    """"Downloads - File Explorer" -> "File Explorer". Usually the app."""
    text = str(title or "").strip()
    return text.rsplit(" - ", 1)[-1].strip() if " - " in text else text


def _app_id(exe: str, title: str = "") -> str:
    """Which app a window belongs to.

    Normally the executable: "brave.exe" -> "brave". Executable names do
    not change with the machine's language, which is what makes this work
    the same everywhere.

    A window hosted by ApplicationFrameHost is the exception - the frame is
    Windows', the app is inside it - so there the title is all we have, and
    its tail is the app's own name.
    """
    stem = os.path.splitext(exe)[0].lower()
    if stem in _HOST_PROCESSES or not stem:
        return _slug(_title_tail(title))
    return _slug(stem)


def open_windows() -> list[dict[str, Any]]:
    """Visible windows, with the app that owns each. Cheap."""
    try:
        from agents.tools.app_tools import list_windows
        raw = list_windows()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for w in raw:
        try:
            hwnd = int(w.get("hwnd") or 0)
        except (TypeError, ValueError):
            continue
        exe = _process_name(hwnd)
        title = str(w.get("title") or "")
        app = _app_id(exe, title)
        if app:
            out.append({"hwnd": hwnd, "title": title, "exe": exe, "app": app})
    return out


class WindowsUIProvider:
    """Anything an open window says it can do."""

    name = "window"

    def __init__(self, windows: Optional[list[dict[str, Any]]] = None,
                 controls=None) -> None:
        self._windows = windows              # injected by tests
        self._controls = controls or uia.controls

    def _open(self) -> list[dict[str, Any]]:
        return self._windows if self._windows is not None else open_windows()

    def apps(self) -> list[AppProfile]:
        out: dict[str, AppProfile] = {}
        for w in self._open():
            app = w["app"]
            if app in out:
                continue
            # The tail of a title is usually the app's own name — "Downloads
            # - File Explorer" — and is what a person would say, so it earns
            # a place as an alias. The executable stays the id.
            title = w["title"]
            tail = _title_tail(title)
            aliases = tuple(a for a in {tail, os.path.splitext(w["exe"])[0]}
                            if a and _slug(a) != app)
            out[app] = AppProfile(app=app, display_name=tail or app,
                                  aliases=aliases, source=self.name,
                                  launch=w["exe"], running=True,
                                  data={"hwnd": w["hwnd"], "title": title})
        return list(out.values())

    def actions(self, app: str) -> list[Action]:
        window = next((w for w in self._open() if w["app"] == app), None)
        if window is None:
            return []
        title = window["title"]
        try:
            found = self._controls(title, limit=MAX_ACTIONS * 2)
        except Exception:  # noqa: BLE001 - a window that will not be read is not a crash
            log.debug("could not read the controls of %r", title, exc_info=True)
            return []

        out: list[Action] = [self._read_text(app, title)]
        seen: set[str] = {out[0].name}
        for row in found:
            name = str(row.get("name") or "").strip()
            kind = str(row.get("type") or "")
            if not name or not row.get("enabled", True):
                continue
            for verb in row.get("actions") or ():
                made = self._make(app, title, name, kind, verb)
                if made is None or made.name in seen:
                    continue
                seen.add(made.name)
                out.append(made)
                if len(out) >= MAX_ACTIONS:
                    return out
        return out

    def _read_text(self, app: str, title: str) -> Action:
        """"What does it say?" — for any app, without knowing the app.

        Every window has labels, and after doing something the answer is
        usually sitting in one of them. Calculator renames its display
        control to "Display is 25", so the action derived from its name
        changes every time the number does; a caller cannot address that.
        Reading the window's text instead is stable, needs no per-app
        knowledge, and is the generic form of "did that work?".
        """
        reader, window = self._controls, title

        async def run() -> str:
            rows = reader(window, limit=MAX_ACTIONS * 3)
            words = [str(r.get("name") or "").strip() for r in rows
                     if str(r.get("type") or "") in _READ_ONLY_TYPES]
            words = [w for w in dict.fromkeys(words) if w]
            if not words:
                return f"nothing readable in {window!r}"
            return f"{window}: " + " | ".join(words[:20])

        return Action(app, "read_text", f"Read the text showing in {window[:40]!r}.",
                      run, Tier.UI_BACKGROUND, Risk.PASSIVE,
                      needs_app_running=True,
                      examples=(f"what does {app} say?",))

    # -- one control, one action ---------------------------------------------

    _VERBS = {"invoke": "click", "toggle": "toggle", "select": "select",
              "expand": "expand", "collapse": "collapse", "set_value": "set",
              "read": "read"}

    def _make(self, app: str, title: str, name: str, kind: str,
              verb: str) -> Optional[Action]:
        prefix = self._VERBS.get(verb)
        if prefix is None:
            return None
        slug = _slug(name)
        if not slug:
            return None
        action_name = f"{prefix}_{slug}"
        control_name, window_title = name, title
        act = self._act

        if verb == "set_value":
            async def run(value: str) -> str:
                return act(window_title, control_name, "set_value", value)
            params: tuple[Param, ...] = (
                Param("value", f"What to put in {name!r}", required=True),)
        else:
            async def run() -> str:                       # noqa: F811
                return act(window_title, control_name, verb)
            params = ()

        return Action(
            app, action_name,
            f"{prefix.capitalize()} {name!r} ({kind}) in {window_title[:40]!r}.",
            run, Tier.UI_BACKGROUND, risk_for(name, kind, verb), params,
            needs_app_running=True,
            examples=(f"{prefix} {name.lower()} in {app}",))

    @staticmethod
    def _act(window: str, control: str, verb: str, value: str = "") -> str:
        try:
            result = uia.act(window, control, verb, value=value)
        except LookupError as exc:
            # It was there when we listed it and is not there now: the app
            # moved on. A clean refusal with a useful message, not a crash.
            raise Refused(str(exc)) from None
        if isinstance(result, dict):
            if result.get("error"):
                raise Refused(str(result["error"]))
            return str(result.get("message") or result.get("result")
                       or f"{verb} {control!r}")
        return str(result)


def providers() -> list[WindowsUIProvider]:
    return [WindowsUIProvider()] if os.name == "nt" else []
