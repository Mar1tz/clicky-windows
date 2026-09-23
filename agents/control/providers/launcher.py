"""Every app on the Start menu, openable by name. No file per app.

`Get-StartApps` is the list Windows itself shows when you press Start: 223
entries on this machine, each with a display name and an AppUserModelID.
That ID is all you need to launch anything — a classic Win32 program, an
MSIX package, a Steam game — through `shell:AppsFolder\\<id>`, which is the
same door the Start menu uses. No install path to find, no shortcut to
parse, no per-app knowledge.

Two things shape the implementation.

**It costs 1.4 seconds.** Far too slow for the catalogue rebuild that runs
every minute, so the list is scanned once and kept in
`%LOCALAPPDATA%\\Tora\\installed_apps.json`. A cold first run pays for it;
everything after reads a file. It is refreshed when it is a day old, which
is about how often somebody installs something.

**Names collide.** "Antigravity" appears twice — a packaged build and an
Electron one — and both are real. The first of a repeated name wins and
the rest are dropped, because offering a person two identical choices is
worse than quietly picking the one Windows lists first.

Opening an app is `Risk.CHANGES`: it puts a window on the user's screen,
so it asks. It is also verified — the window either appeared or it did
not, and saying "opened Notepad" when nothing opened is the class of lie
DC-24 exists to stop.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any, Optional

from ..model import Action, Refused, Risk, Tier, Unverified
from ..provider import AppProfile

log = logging.getLogger("agents.control")

CACHE_NAME = "installed_apps.json"
CACHE_MAX_AGE_S = 24 * 60 * 60
SCAN_TIMEOUT_S = 90.0
# How long to give a launched app to put a window up before we call it a
# failure. Cold-starting a big app takes a while; this is a compromise
# that catches "nothing happened" without punishing Photoshop.
APPEAR_TIMEOUT_S = 6.0

_PS_LIST = "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"


def _cache_path() -> str:
    override = os.environ.get("TORA_APPS_CACHE")
    if override:
        return override
    try:
        from config import data_dir
        return str(data_dir() / CACHE_NAME)
    except Exception:  # noqa: BLE001 - the agent package must not need config
        return ""


def _run_powershell(script: str) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=SCAN_TIMEOUT_S,
        encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out.stdout or ""


def scan_installed() -> list[dict[str, str]]:
    """Ask Windows for its own app list. Slow; call through `installed()`."""
    raw = _run_powershell(_PS_LIST).strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):            # a single app comes back unwrapped
        data = [data]
    out = []
    for item in data:
        name = str(item.get("Name") or "").strip()
        app_id = str(item.get("AppID") or "").strip()
        if name and app_id:
            out.append({"name": name, "id": app_id})
    return out


def installed(refresh: bool = False) -> list[dict[str, str]]:
    """The app list, from cache unless it is missing, stale or forced."""
    path = _cache_path()
    if path and not refresh and os.path.exists(path):
        try:
            age = time.time() - os.path.getmtime(path)
            if age < CACHE_MAX_AGE_S:
                with open(path, "r", encoding="utf-8") as handle:
                    cached = json.load(handle)
                if isinstance(cached, list) and cached:
                    return cached
        except Exception:  # noqa: BLE001 - a bad cache just means rescan
            log.debug("could not read %s; rescanning", path, exc_info=True)
    try:
        found = scan_installed()
    except Exception:  # noqa: BLE001 - no PowerShell, no list, no crash
        log.warning("could not list installed apps", exc_info=True)
        return []
    if found and path:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(found, handle, indent=1)
        except Exception:  # noqa: BLE001
            log.debug("could not cache the app list", exc_info=True)
    return found


def app_id_for(app: str) -> str:
    """The AppUserModelID for an app id produced by this provider."""
    for entry in installed():
        if _slug(entry["name"]) == app:
            return entry["id"]
    return ""


def _slug(name: str) -> str:
    """"Microsoft Edge" -> "microsoft_edge". The id the model says."""
    keep = [c.lower() if c.isalnum() else " " for c in str(name)]
    return "_".join("".join(keep).split())


def launch(app_id: str) -> None:
    """Open an app the way the Start menu does."""
    if not app_id:
        raise Refused("no app id to open")
    os.startfile(f"shell:AppsFolder\\{app_id}")


def _window_titles() -> list[str]:
    try:
        from agents.tools.app_tools import list_windows
        return [str(w.get("title") or "") for w in list_windows()]
    except Exception:  # noqa: BLE001
        return []


class LauncherProvider:
    """Opening anything Windows can open."""

    name = "launcher"

    def __init__(self, entries: Optional[list[dict[str, str]]] = None) -> None:
        self._entries = entries          # injected by tests; None = the real list

    def _list(self) -> list[dict[str, str]]:
        return self._entries if self._entries is not None else installed()

    def apps(self) -> list[AppProfile]:
        out: list[AppProfile] = []
        seen: set[str] = set()
        for entry in self._list():
            app = _slug(entry["name"])
            if not app or app in seen:
                # Two entries, one name ("Antigravity" packaged and
                # Electron). Windows lists its preferred one first.
                continue
            seen.add(app)
            out.append(AppProfile(app=app, display_name=entry["name"],
                                  source=self.name, launch=entry["id"]))
        return out

    def actions(self, app: str) -> list[Action]:
        app_id = ""
        display = app
        for entry in self._list():
            if _slug(entry["name"]) == app:
                app_id, display = entry["id"], entry["name"]
                break
        if not app_id:
            return []

        async def open_app() -> str:
            before = set(_window_titles())
            launch(app_id)
            return f"opened {display}"

        async def snapshot() -> Any:
            return set(_window_titles())

        async def verify(before: Any = None) -> Optional[str]:
            """Did a window actually appear?

            An app that was already running puts no new window up, and that
            is not a failure — it is "it is already open". Only the case
            where nothing was running and nothing appeared is a real one.
            """
            import asyncio
            deadline = time.monotonic() + APPEAR_TIMEOUT_S
            was = set(before or ())
            wanted = display.lower()
            if any(wanted in t.lower() for t in was):
                return f"{display} was already open"
            while time.monotonic() < deadline:
                await asyncio.sleep(0.4)
                now = set(_window_titles())
                fresh = now - was
                if any(wanted in t.lower() for t in fresh):
                    return f"{display} is open"
                if fresh:
                    return f"{display} is open ({sorted(fresh)[0][:40]})"
            raise Unverified(f"{display} did not put a window up")

        return [Action(app, "open", f"Open {display}.", open_app,
                       Tier.SYSTEM, Risk.CHANGES,
                       snapshot=snapshot, verify=verify,
                       examples=(f"open {display.lower()}",))]


def providers() -> list[LauncherProvider]:
    return [LauncherProvider()] if os.name == "nt" else []
