"""Browsers: open a page in a named browser, a new window, or private mode.

Every browser takes a URL and a couple of flags on its command line, which
is quieter and more exact than clicking a taskbar icon and typing. Driving
a page once it is open is a different job and belongs to the Playwright
browser tools; this is the "put this in front of me" half.
"""
from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass

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

    def _launch(self, url: str, *, private: bool = False, new_window: bool = False) -> None:
        path = self.path()
        if not path:
            raise Unavailable(f"{self.display_name} is not installed here")
        argv = [path]
        if private:
            argv.append(self.private_flag)
        if new_window:
            argv.append("-new-window" if self.app == "firefox" else "--new-window")
        argv.append(url)
        if path.lower().endswith(".lnk"):
            # A shortcut cannot take arguments: hand the URL to Windows and
            # let the browser that owns http: take it, or start the shortcut.
            os.startfile(path, arguments=" ".join(argv[1:])) if hasattr(os, "startfile") else None
            return
        run_cli(argv, timeout=10)

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


def adapters() -> list[Browser]:
    out = []
    for app, display, stem, flag in BROWSERS:
        b = Browser(app, display, stem, flag, aliases=(display.lower(), stem.lower()))
        if b.available():
            out.append(b)
    return out
