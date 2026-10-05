"""Browsers: open a page in a named browser, a new window, or private mode.

Every browser takes a URL and a couple of flags on its command line, which
is quieter and more exact than clicking a taskbar icon and typing. Driving
a page once it is open is a different job and belongs to the Playwright
browser tools; this is the "put this in front of me" half.

"browser" is the one the user is USING: the browser window they were in
most recently, else their default browser. "Open Gmail" with Chrome open
opened Gmail in Edge (5 Oct 2026, a tester's PC): the tool listed Edge but
not Chrome, and a site with no browser named went to Windows' default. A
page now opens as a new tab in the browser already in front of them, and a
named browser is used only when the user named it (companion_manager
checks what they said).
"""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from ..discovery import open_uri, run_cli, scan
from ..model import Action, Param, Refused, Risk, Tier, Unavailable

# app id, display name, Start Menu name, private-mode flag
BROWSERS = (
    ("chrome", "Google Chrome", "Chrome", "--incognito"),
    ("edge", "Microsoft Edge", "Edge", "--inprivate"),
    ("brave", "Brave", "Brave", "--incognito"),
    ("opera", "Opera", "Opera", "--private"),
    ("firefox", "Firefox", "Firefox", "-private-window"),
    ("vivaldi", "Vivaldi", "Vivaldi", "--incognito"),
)

SEARCH = "https://www.google.com/search?q="

# A browser's own program, as Windows runs it.
EXES = {"chrome.exe": "chrome", "msedge.exe": "edge", "brave.exe": "brave", "opera.exe": "opera",
        "firefox.exe": "firefox", "vivaldi.exe": "vivaldi"}
# What the default-browser setting (its ProgId) says, -> the browser.
PROGIDS = (("chrome", "chrome"), ("msedge", "edge"), ("brave", "brave"), ("opera", "opera"),
           ("firefox", "firefox"), ("vivaldi", "vivaldi"))
NAMES = {app: (display, stem) for app, display, stem, _flag in BROWSERS}


def browser_windows() -> list[tuple[str, str]]:
    """(browser, its program) for each browser with a window open, the one
    the user was in most recently first (Windows lists windows front to back)."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    found: list[tuple[str, str]] = []

    def visit(hwnd, _lparam):
        try:
            if (not user32.IsWindowVisible(hwnd) or not user32.GetWindowTextLengthW(hwnd)
                    or user32.GetWindow(hwnd, 4)):           # GW_OWNER: a popup, not a browser window
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = kernel32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
            if not handle:
                return True
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(len(buf))
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    app = EXES.get(os.path.basename(buf.value).lower())
                    if app and all(app != a for a, _p in found):
                        found.append((app, buf.value))
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001 - one odd window must not stop the walk
            pass
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    return found


def default_browser() -> str:
    """The browser Windows opens links in, or "" if it can't be told."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        key = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            prog = str(winreg.QueryValueEx(k, "ProgId")[0]).lower()
    except OSError:
        return ""
    return next((app for marker, app in PROGIDS if marker in prog), "")


def in_use() -> tuple[str, str]:
    """(browser, its running program) the user is using right now: the one
    they were in most recently, else their default browser (no program then),
    else ("", "")."""
    open_now = browser_windows()
    if open_now:
        return open_now[0]
    return default_browser(), ""


def _spawn(argv: list[str]) -> None:
    """Start a browser and let it be: never wait for it, never stop it. (A
    browser that was not running yet IS the process, and run_cli's timeout
    used to kill it ten seconds after it opened.)"""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(argv, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@dataclass
class Browser:
    app: str
    display_name: str
    start_menu: str
    private_flag: str
    aliases: tuple[str, ...] = ()

    def path(self) -> str:
        from agents.tools.app_tools import find_app
        found = find_app(self.start_menu)
        return found[1] if found else ""

    def available(self) -> bool:
        return bool(self.path())

    def _launch(self, url: str, *, private: bool = False, new_window: bool = False,
                program: str = "") -> None:
        """Open `url` - a new tab when the browser is already open. `program`:
        the running browser's own exe, when it is known."""
        path = program or self.path()
        if not path:
            raise Unavailable(f"{self.display_name} is not installed here")
        argv = [path]
        if private:
            argv.append(self.private_flag)
        if new_window:
            argv.append("-new-window" if self.app == "firefox" else "--new-window")
        elif self.app == "firefox":
            argv.append("-new-tab")
        argv.append(url)
        if path.lower().endswith(".lnk"):
            # A shortcut cannot take arguments: hand the URL to Windows and
            # let the browser that owns http: take it, or start the shortcut.
            os.startfile(path, arguments=" ".join(argv[1:])) if hasattr(os, "startfile") else None
            return
        _spawn(argv)

    def actions(self) -> list[Action]:
        async def open_url(url: str, private: bool = False, new_window: bool = False) -> str:
            if not url.startswith(("http://", "https://", "file:")):
                url = "https://" + url
            self._launch(url, private=bool(private), new_window=bool(new_window))
            return f"opened {url} in {self.display_name}" + (" (private window)" if private else "")

        async def search(query: str, private: bool = False) -> str:
            self._launch(SEARCH + urllib.parse.quote(query), private=bool(private))
            return f"searched {self.display_name} for {query!r}"

        return [
            Action(self.app, "open_url", f"Open a web address in {self.display_name}.", open_url,
                   Tier.API, Risk.CHANGES,
                   (Param("url", "The address", True), Param("private", "Private window", type="boolean"),
                    Param("new_window", "New window", type="boolean")),
                   examples=(f"open github.com in {self.display_name}",)),
            Action(self.app, "search", f"Search the web in {self.display_name}.", search, Tier.API,
                   Risk.CHANGES, (Param("query", "What to search for", True),
                                  Param("private", "Private window", type="boolean"))),
        ]


def browser_for(app: str) -> Optional[Browser]:
    for a, display, stem, flag in BROWSERS:
        if a == app:
            return Browser(a, display, stem, flag, aliases=(display.lower(), stem.lower()))
    return None


def open_in_use(url: str, *, private: bool = False, new_window: bool = False) -> str:
    """Open `url` in the browser the user is using; says which, in words."""
    app, program = in_use()
    browser = browser_for(app) if app else None
    if browser is not None and (program or browser.available()):
        browser._launch(url, private=private, new_window=new_window, program=program)
        how = "the browser you have open" if program else "your default browser"
        return f"{browser.display_name}, {how}"
    open_uri(url)                                    # not one we know: Windows' own default
    return "your default browser"


class InUseBrowser:
    """"browser": whichever browser the user is using (see in_use)."""

    app = "browser"
    display_name = "Your browser"
    aliases = ("web browser", "my browser", "the browser", "default browser", "internet")

    def available(self) -> bool:
        return True

    def actions(self) -> list[Action]:
        async def open_url(url: str, private: bool = False, new_window: bool = False) -> str:
            if not url.startswith(("http://", "https://", "file:")):
                url = "https://" + url
            where = open_in_use(url, private=bool(private), new_window=bool(new_window))
            return f"opened {url} in {where}" + (" (private window)" if private else "")

        async def search(query: str, private: bool = False) -> str:
            where = open_in_use(SEARCH + urllib.parse.quote(query), private=bool(private))
            return f"searched for {query!r} in {where}"

        return [
            Action(self.app, "open_url", "Open a web address in the browser the user already has "
                   "open (a new tab), or their default browser. Use this for any website unless "
                   "the user named a browser.", open_url, Tier.API, Risk.CHANGES,
                   (Param("url", "The address", True), Param("private", "Private window", type="boolean"),
                    Param("new_window", "New window", type="boolean")),
                   examples=("open gmail", "open github.com")),
            Action(self.app, "search", "Search the web in the browser the user already has open.",
                   search, Tier.API, Risk.CHANGES,
                   (Param("query", "What to search for", True),
                    Param("private", "Private window", type="boolean"))),
        ]


def adapters() -> list:
    out: list = [InUseBrowser()]
    for app, display, stem, flag in BROWSERS:
        b = Browser(app, display, stem, flag, aliases=(display.lower(), stem.lower()))
        if b.available():
            out.append(b)
    return out
