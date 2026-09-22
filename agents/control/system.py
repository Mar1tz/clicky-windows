"""What the operating system will do for us - with nobody's focus stolen.

Media keys are the trick that makes "Spotify, next song" instant and
invisible: Windows routes VK_MEDIA_NEXT_TRACK to whichever app owns the
current media session, so the app can be minimised, on another desktop,
or behind a full-screen game, and the cursor never moves. Same for
volume. Window management and the clipboard go through Win32 calls, not
through clicking.

Everything here is injectable (`set_backend`) so tests never press a real
key, and everything is stopped by the same kill switch as the mouse
tools: Esc trips it, every desktop action fails until a new task starts.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger("agents.control.system")

IS_WINDOWS = os.name == "nt"
_user32 = ctypes.windll.user32 if IS_WINDOWS else None
_kernel32 = ctypes.windll.kernel32 if IS_WINDOWS else None

# Virtual key codes Windows routes to the media session, whatever has focus.
VK = {
    "media_next": 0xB0, "media_previous": 0xB1, "media_stop": 0xB2, "media_play_pause": 0xB3,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
}
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE, SW_SHOW = 6, 3, 9, 5
WM_CLOSE = 0x0010


class Backend:
    """The real keystrokes and window calls, so tests can swap them out."""

    def tap(self, vk: int) -> None:
        if _user32 is None:
            raise RuntimeError("media and volume keys are a Windows path")
        _user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
        _user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)

    def show_window(self, hwnd: int, how: int) -> None:
        _user32.ShowWindow(wintypes.HWND(hwnd), how)

    def close_window(self, hwnd: int) -> None:
        _user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)


_backend: Optional[Backend] = None


def get_backend() -> Backend:
    global _backend
    if _backend is None:
        _backend = Backend()
    return _backend


def set_backend(backend: Optional[Backend]) -> None:
    global _backend
    _backend = backend


def tap(key: str, times: int = 1) -> None:
    """Press a media or volume key `times`. No focus change, no cursor."""
    vk = VK.get(key)
    if vk is None:
        raise ValueError(f"unknown system key {key!r}; one of {', '.join(VK)}")
    backend = get_backend()
    for i in range(max(1, min(int(times), 50))):
        if i:
            time.sleep(0.03)
        backend.tap(vk)


# -- what is playing ---------------------------------------------------------------

@dataclass
class NowPlaying:
    app: str = ""
    title: str = ""
    artist: str = ""
    playing: bool = False
    source: str = ""

    def describe(self) -> str:
        if not self.app:
            return "nothing is playing"
        if not self.title:
            return f"{self.app} is open but nothing is playing"
        who = f"{self.title} - {self.artist}" if self.artist else self.title
        return f"{'Playing' if self.playing else 'Paused'}: {who} ({self.app})"


# Media apps whose window title carries the track while it plays. Spotify's
# title is "Artist - Track" playing and "Spotify Premium"/"Spotify Free"
# when paused, which is enough to answer "what's playing?" with no
# dependency, no account and no network.
_MEDIA_APPS = {
    "spotify": ("Spotify Premium", "Spotify Free", "Spotify"),
    "vlc": ("VLC media player",),
    "foobar2000": ("foobar2000",),
    "music": ("Media Player", "Groove Music"),
    "youtube music": ("YouTube Music",),
    "itunes": ("iTunes",),
}


def now_playing() -> NowPlaying:
    """Read the current track from the media app's window title.

    Tried first because it needs nothing: no WinRT package, no Spotify
    account, no network call. `winrt` (the system media session) would be
    richer and is the natural Phase 2 upgrade.
    """
    try:
        from agents.tools.app_tools import list_windows
        windows = list_windows()
    except Exception:  # noqa: BLE001
        return NowPlaying()
    for w in windows:
        title = (w.get("title") or "").strip()
        for app, idles in _MEDIA_APPS.items():
            if not any(title.lower().startswith(idle.lower()) or title.lower() == idle.lower()
                       for idle in idles) and app not in title.lower():
                continue
            idle = any(title.lower() == i.lower() for i in idles)
            if idle:
                return NowPlaying(app=app.title(), playing=False, source="window title")
            artist, _, track = title.partition(" - ")
            if track:
                return NowPlaying(app=app.title(), title=track.strip(), artist=artist.strip(),
                                  playing=True, source="window title")
            return NowPlaying(app=app.title(), title=title, playing=True, source="window title")
    return NowPlaying()


# -- windows -------------------------------------------------------------------------

def find_window(fragment: str) -> Optional[dict[str, Any]]:
    from agents.tools.app_tools import list_windows
    frag = (fragment or "").strip().lower()
    if not frag:
        return None
    windows = list_windows()
    for w in windows:                                   # exact first, then contains
        if w["title"].strip().lower() == frag:
            return w
    for w in windows:
        if frag in w["title"].lower():
            return w
    return None


def window_command(fragment: str, how: str) -> str:
    """minimise / maximise / restore / close / focus a window by title."""
    w = find_window(fragment)
    if w is None:
        from agents.tools.app_tools import list_windows
        open_now = ", ".join(x["title"][:40] for x in list_windows()[:8]) or "nothing"
        raise LookupError(f"no open window matches {fragment!r}. Open: {open_now}")
    backend = get_backend()
    how = how.lower()
    if how in ("minimise", "minimize", "hide"):
        backend.show_window(w["hwnd"], SW_MINIMIZE)
        return f"minimised {w['title']}"
    if how in ("maximise", "maximize", "full"):
        backend.show_window(w["hwnd"], SW_MAXIMIZE)
        return f"maximised {w['title']}"
    if how in ("restore", "show"):
        backend.show_window(w["hwnd"], SW_RESTORE)
        return f"restored {w['title']}"
    if how == "close":
        backend.close_window(w["hwnd"])
        return f"asked {w['title']} to close"
    if how == "focus":
        from agents.tools.app_tools import focus_window
        got = focus_window(w["title"])
        return f"brought {w['title']} to the front" if got else f"could not focus {w['title']}"
    raise ValueError(f"unknown window command {how!r}")


# -- clipboard ------------------------------------------------------------------------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def clipboard_read(limit: int = 20000) -> str:
    if _user32 is None:
        raise RuntimeError("the clipboard is a Windows path here")
    if not _user32.OpenClipboard(None):
        raise RuntimeError("another app is holding the clipboard")
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _kernel32.GlobalLock(ctypes.c_void_p(handle))
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value[:limit] or ""
        finally:
            _kernel32.GlobalUnlock(ctypes.c_void_p(handle))
    finally:
        _user32.CloseClipboard()


def clipboard_write(text: str) -> None:
    if _user32 is None:
        raise RuntimeError("the clipboard is a Windows path here")
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    if not _user32.OpenClipboard(None):
        raise RuntimeError("another app is holding the clipboard")
    try:
        _user32.EmptyClipboard()
        handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        ptr = _kernel32.GlobalLock(ctypes.c_void_p(handle))
        ctypes.memmove(ptr, data, size)
        _kernel32.GlobalUnlock(ctypes.c_void_p(handle))
        _user32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(handle))
    finally:
        _user32.CloseClipboard()
