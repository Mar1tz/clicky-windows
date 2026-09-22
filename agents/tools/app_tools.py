"""Applications - open one, see what is open, bring one to the front.

"Open Devin" should be one step, not Win key + type + Enter with an
approval on each. Windows already knows every installed app by name: the
Start Menu is a folder of shortcuts, so `open_app` searches it (per-user
and all-users), falls back to anything on PATH, and launches the match
the same way a click on the Start Menu would. MEDIUM risk - opening an
app is on the order of running a command, not of driving the mouse.

`list_windows` and `focus_window` read and raise top-level windows
through the Win32 API directly, which is instant and needs nothing on
screen to be named. Both are the cheap half of computer control: an
agent that can see what is open and bring the right window forward
rarely needs to move the cursor at all.
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from typing import Any, Optional

from agents.core import RiskLevel, ToolContext, ToolResult

from .base import Tool, make_spec

CATEGORY = "computer"
APPEAR_TIMEOUT_S = 6.0

# Shortcuts an app search should ignore: uninstallers and docs, not apps.
# "help" must be a whole word - "DevinTools Helper" is an app.
import re

_SKIP = re.compile(r"\buninstall|\breadme\b|\bhelp\b|\bdocumentation\b|\blicen[cs]e\b|\bwebsite\b", re.I)


def _is_junk(stem: str) -> bool:
    return bool(_SKIP.search(stem))


def start_menu_dirs() -> list[str]:
    dirs = []
    for base in (os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")):
        if base:
            dirs.append(os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs"))
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        dirs.append(desktop)
    return [d for d in dirs if os.path.isdir(d)]


def find_app(name: str) -> Optional[tuple[str, str]]:
    """(display name, launch path) for the best Start Menu match, or None.

    Exact stem match wins, then a stem that starts with the query, then a
    substring - so "Devin" picks Devin.lnk over "Devin Uninstaller.lnk".
    """
    query = name.strip().lower()
    if not query:
        return None
    exact, prefix, contains = [], [], []
    for root_dir in start_menu_dirs():
        for dirpath, _dirs, files in os.walk(root_dir):
            for f in files:
                if not f.lower().endswith((".lnk", ".url", ".appref-ms")):
                    continue
                stem = os.path.splitext(f)[0]
                low = stem.lower()
                if _is_junk(stem):
                    continue
                full = os.path.join(dirpath, f)
                if low == query:
                    exact.append((stem, full))
                elif low.startswith(query):
                    prefix.append((stem, full))
                elif query in low:
                    contains.append((stem, full))
    for bucket in (exact, prefix, contains):
        if bucket:
            bucket.sort(key=lambda t: len(t[0]))
            return bucket[0]
    exe = shutil.which(query) or shutil.which(query + ".exe")
    if exe:
        return os.path.splitext(os.path.basename(exe))[0], exe
    return None


def installed_apps(limit: int = 60) -> list[str]:
    names: set[str] = set()
    for root_dir in start_menu_dirs():
        for _d, _dirs, files in os.walk(root_dir):
            for f in files:
                if f.lower().endswith(".lnk"):
                    stem = os.path.splitext(f)[0]
                    if not _is_junk(stem):
                        names.add(stem)
    return sorted(names, key=str.lower)[:limit]


# -- Win32 windows -------------------------------------------------------------

_user32 = ctypes.windll.user32 if os.name == "nt" else None


def list_windows() -> list[dict[str, Any]]:
    """Visible top-level windows with a title, front-most first."""
    if _user32 is None:
        return []
    out: list[dict[str, Any]] = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _each(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if title and title not in ("Program Manager", "Windows Input Experience"):
            out.append({"hwnd": int(hwnd), "title": title})
        return True

    _user32.EnumWindows(proc(_each), 0)
    return out


def focus_window(title_fragment: str) -> Optional[dict[str, Any]]:
    if _user32 is None:
        return None
    frag = title_fragment.lower()
    for w in list_windows():
        if frag in w["title"].lower():
            try:
                _user32.ShowWindow(w["hwnd"], 9)            # SW_RESTORE
                _user32.SetForegroundWindow(w["hwnd"])
            except Exception:  # noqa: BLE001
                pass
            return w
    return None


def _launch(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)                                    # what a Start Menu click does
    else:
        subprocess.Popen([path])


async def _wait_for_window(fragment: str, timeout: float) -> Optional[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    frag = fragment.lower()
    while time.monotonic() < deadline:
        for w in await asyncio.to_thread(list_windows):
            if frag in w["title"].lower():
                return w
        await asyncio.sleep(0.25)
    return None


# -- tools ---------------------------------------------------------------------

class OpenApp(Tool):
    spec = make_spec(
        "open_app",
        "Open an installed application by name (as it appears in the Start Menu), "
        "or bring it to the front if it is already open. One step - never press "
        "the Windows key and type to open an app.",
        risk=RiskLevel.MEDIUM, category=CATEGORY,
        properties={"name": {"type": "string", "description": "e.g. 'Notepad', 'Devin', 'Chrome'."}},
        required=["name"],
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = str(args.get("name") or "").strip()
        if not name:
            return self.fail("give the app's name")
        already = await asyncio.to_thread(focus_window, name)
        if already is not None:
            return self.ok(f"{already['title']!r} was already open; brought it to the front",
                           title=already["title"], launched=False)
        found = await asyncio.to_thread(find_app, name)
        if found is None:
            apps = await asyncio.to_thread(installed_apps)
            near = [a for a in apps if name.lower()[:3] in a.lower()][:8]
            return self.fail(f"no installed app called {name!r}. "
                             + (f"Similar: {', '.join(near)}. " if near else "")
                             + "Use list_windows to see what is open.",
                             installed=apps[:40])
        display, path = found
        try:
            await asyncio.to_thread(_launch, path)
        except OSError as exc:
            return self.fail(f"could not launch {display}: {exc}")
        window = await _wait_for_window(display.split()[0], APPEAR_TIMEOUT_S)
        if window is None:
            return self.ok(f"launched {display} (no window seen yet - give it a moment, "
                           f"then list_windows)", app=display, path=path, launched=True)
        await asyncio.to_thread(focus_window, window["title"])
        return self.ok(f"opened {display}: window {window['title']!r} is in front",
                       app=display, path=path, title=window["title"], launched=True)


class ListWindows(Tool):
    spec = make_spec(
        "list_windows", "List the open windows, front-most first. Read-only.",
        risk=RiskLevel.SAFE, category=CATEGORY, properties={},
    )

    async def run(self, args, ctx):
        wins = await asyncio.to_thread(list_windows)
        titles = [w["title"] for w in wins][:40]
        return self.ok("\n".join(titles) or "(no windows)", windows=titles, count=len(wins))


class FocusWindow(Tool):
    spec = make_spec(
        "focus_window", "Bring a window to the front by (part of) its title.",
        risk=RiskLevel.LOW, category=CATEGORY,
        properties={"title": {"type": "string", "description": "Part of the window title."}},
        required=["title"],
    )

    async def run(self, args, ctx):
        frag = str(args.get("title") or "").strip()
        w = await asyncio.to_thread(focus_window, frag)
        if w is None:
            return self.fail(f"no open window matches {frag!r}; use list_windows")
        return self.ok(f"{w['title']!r} is in front", title=w["title"])


def tools() -> list[Tool]:
    return [OpenApp(), ListWindows(), FocusWindow()]
