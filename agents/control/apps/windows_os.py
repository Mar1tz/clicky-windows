"""Windows itself: media, volume, windows, clipboard, folders, settings, apps.

The everyday requests that are not about one app - "turn it down", "put
this on the clipboard", "minimise that", "open my downloads", "open
Bluetooth settings", "open Excel" - all of which Windows will do through
an API. None of them touches the cursor.

`ms-settings:` deep links are the reason "open Bluetooth settings" is one
call instead of four clicks; the list below is the common ones, and any
other page can be reached by passing its name.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from typing import Optional

from .. import audio, system
from ..discovery import open_uri, scan
from ..model import Action, Param, Refused, Risk, Tier, Unavailable

SETTINGS_PAGES = {
    "bluetooth": "bluetooth", "wifi": "network-wifi", "network": "network", "display": "display",
    "sound": "sound", "audio": "sound", "notifications": "notifications", "power": "powersleep",
    "battery": "batterysaver", "storage": "storagesense", "apps": "appsfeatures",
    "default apps": "defaultapps", "startup": "startupapps", "printers": "printers",
    "privacy": "privacy", "microphone": "privacy-microphone", "camera": "privacy-webcam",
    "windows update": "windowsupdate", "update": "windowsupdate", "about": "about",
    "mouse": "mousetouchpad", "keyboard": "keyboard", "language": "regionlanguage",
    "personalisation": "personalization", "background": "personalization-background",
    "taskbar": "taskbar", "developer": "developers", "accounts": "yourinfo",
}

FOLDERS = {
    "downloads": r"%USERPROFILE%\Downloads", "documents": r"%USERPROFILE%\Documents",
    "desktop": r"%USERPROFILE%\Desktop", "pictures": r"%USERPROFILE%\Pictures",
    "music": r"%USERPROFILE%\Music", "videos": r"%USERPROFILE%\Videos",
    "home": r"%USERPROFILE%", "appdata": r"%APPDATA%", "temp": r"%TEMP%",
}


@dataclass
class WindowsOS:
    app: str = "windows"
    display_name: str = "Windows"
    aliases: tuple[str, ...] = ("system", "pc", "computer", "desktop")

    def available(self) -> bool:
        return os.name == "nt"

    def actions(self) -> list[Action]:
        async def media(what: str, times: int = 1) -> str:
            # No default: `what` IS the verb here. Defaulting it meant a
            # dropped argument turned "next song" into a pause.
            key = what if what.startswith("media_") else f"media_{what}"
            system.tap(key, int(times or 1))
            now = system.now_playing()
            return f"{what.replace('_', ' ')}" + (f"; {now.describe().lower()}" if now.title else "")

        async def volume(direction: str, steps: int = 2, to_percent: int = 0) -> str:
            # No default for `direction`: a lost argument must be an error,
            # never the opposite action. See agents/control/args.py.
            #
            # By VALUE where the machine allows it. Tapping a key N times
            # cannot honour "set it to twenty percent", cannot know where it
            # started, and cannot tell afterwards whether anything heard the
            # key. audio.py reads and writes the endpoint directly; the key
            # taps stay as the fallback for a machine with no endpoint.
            if direction == "mute":
                try:
                    now = audio.set_muted(not audio.muted())
                    return "muted" if now else "unmuted"
                except Unavailable:
                    system.tap("volume_mute")
                    return "muted (say it again to unmute)"

            steps = max(1, min(int(steps or 2), 20))
            try:
                before = audio.level()
            except Unavailable:
                system.tap("volume_up" if direction == "up" else "volume_down", steps)
                return f"turned the volume {direction} {steps} notches"

            if to_percent:
                target = max(0, min(int(to_percent), 100)) / 100.0
            else:
                delta = 0.02 * steps
                target = before + delta if direction == "up" else before - delta
            after = audio.set_level(target)
            return f"volume {direction} - now at {audio.describe(after)}"

        async def verify_volume(direction: str, steps: int = 2,
                                to_percent: int = 0) -> Optional[str]:
            """What the volume IS now, read back from the device."""
            if direction == "mute":
                try:
                    return "muted" if audio.muted() else "unmuted"
                except Unavailable:
                    return None
            try:
                return f"volume is at {audio.describe(audio.level())}"
            except Unavailable:
                return None            # a key tap cannot be checked; say so

        async def now_playing() -> str:
            return system.now_playing().describe()

        async def window(command: str, title: str) -> str:
            return system.window_command(title, command)

        async def list_open_windows() -> str:
            from agents.tools.app_tools import list_windows
            titles = [w["title"] for w in list_windows()]
            return f"{len(titles)} open windows: " + " | ".join(t[:50] for t in titles[:15])

        async def clipboard_copy(text: str) -> str:
            system.clipboard_write(text)
            return f"copied {len(text)} characters to the clipboard"

        async def clipboard_read() -> str:
            text = system.clipboard_read()
            return f"clipboard: {text[:500]}" if text else "the clipboard is empty"

        async def open_folder(folder: str) -> str:
            path = FOLDERS.get(folder.strip().lower(), folder)
            full = os.path.expandvars(os.path.expanduser(path))
            if not os.path.isdir(full):
                raise Refused(f"no folder at {full}")
            os.startfile(full)
            return f"opened {full} in File Explorer"

        async def open_settings(page: str = "") -> str:
            key = page.strip().lower()
            target = SETTINGS_PAGES.get(key, key.replace(" ", ""))
            open_uri(f"ms-settings:{target}" if target else "ms-settings:")
            return f"opened Windows Settings{f' at {page}' if page else ''}"

        async def open_app(name: str) -> str:
            from agents.tools.app_tools import find_app
            found = find_app(name)
            if not found:
                apps = ", ".join(list(scan().apps)[:12])
                raise Refused(f"no app called {name!r}. Installed: {apps}…")
            os.startfile(found[1])
            return f"opened {found[0]}"

        async def open_url(url: str) -> str:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            open_uri(url)
            return f"opened {url}"

        async def search_web(query: str) -> str:
            import urllib.parse
            open_uri("https://www.google.com/search?q=" + urllib.parse.quote(query))
            return f"searched the web for {query!r} in your browser"

        return [
            Action(self.app, "media", "Play/pause, next or previous track for whatever is playing.",
                   media, Tier.SYSTEM, Risk.REVERSIBLE,
                   (Param("what", "play_pause | next | previous | stop", required=True,
                          choices=("play_pause", "next", "previous", "stop")),
                    Param("times", "How many times", type="integer")),
                   examples=("next song", "pause the music")),
            Action(self.app, "volume", "Turn the volume up or down, set it to a "
                   "percentage, or mute it.", volume, Tier.SYSTEM, Risk.REVERSIBLE,
                   (Param("direction", "up, down or mute", required=True,
                          choices=("up", "down", "mute")),
                    Param("steps", "How much, in small notches", type="integer"),
                    Param("to_percent", "Set it to exactly this, 0-100", type="integer")),
                   verify=verify_volume,
                   examples=("turn it down", "set the volume to 20 percent")),
            Action(self.app, "now_playing", "What is playing right now.", now_playing, Tier.SYSTEM,
                   Risk.PASSIVE),
            Action(self.app, "window", "Focus, minimise, maximise, restore or close a window by title.",
                   window, Tier.SYSTEM, Risk.REVERSIBLE,
                   (Param("command", "focus | minimise | maximise | restore | close", True,
                          choices=("focus", "minimise", "maximise", "restore", "close")),
                    Param("title", "Part of the window title", True))),
            Action(self.app, "list_windows", "Every open window.", list_open_windows, Tier.SYSTEM,
                   Risk.PASSIVE),
            Action(self.app, "clipboard_copy", "Put text on the clipboard.", clipboard_copy, Tier.SYSTEM,
                   Risk.CHANGES, (Param("text", "What to copy", True),)),
            Action(self.app, "clipboard_read", "Read the clipboard.", clipboard_read, Tier.SYSTEM,
                   Risk.PASSIVE),
            Action(self.app, "open_folder", "Open a folder in File Explorer (downloads, documents, or a path).",
                   open_folder, Tier.SYSTEM, Risk.CHANGES, (Param("folder", "Name or path", True),)),
            Action(self.app, "open_settings", "Open Windows Settings, optionally at a page.", open_settings,
                   Tier.DEEP_LINK, Risk.CHANGES,
                   (Param("page", "bluetooth, wifi, display, sound, update…"),)),
            Action(self.app, "open_app", "Open an installed application by name.", open_app,
                   Tier.SYSTEM, Risk.CHANGES, (Param("name", "Application name", True),)),
            Action(self.app, "open_url", "Open a web address in the default browser.", open_url,
                   Tier.DEEP_LINK, Risk.CHANGES, (Param("url", "The address", True),)),
            Action(self.app, "search_web", "Search the web in the default browser.", search_web,
                   Tier.DEEP_LINK, Risk.CHANGES, (Param("query", "What to search for", True),)),
        ]


def adapters() -> list[WindowsOS]:
    w = WindowsOS()
    return [w] if w.available() else []
