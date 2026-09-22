"""What this machine can actually be asked to do.

Three questions, answered from the machine itself rather than from a
list someone wrote in advance:

  * which apps are installed (Start Menu shortcuts and PATH)
  * which apps can be reached by a link - every `URL Protocol` key in
    the registry is an app saying "send me spotify:…, vscode://…,
    obsidian://…" and that is the quietest way to drive it
  * which command-line tools exist (`code`, `gh`, `npm`, `winget`…)

The answers are cached for a minute: they change when someone installs
something, not between two tool calls, and a registry walk is not free.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

IS_WINDOWS = os.name == "nt"
CACHE_S = 60.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0

# Schemes every Windows has and that say nothing about what is installed.
_DULL = {"http", "https", "file", "ftp", "mailto", "callto", "tel", "sms", "shell",
         "microsoft-edge", "search-ms", "ms-search", "res", "javascript", "vbscript"}


@dataclass
class Machine:
    apps: dict[str, str] = field(default_factory=dict)        # display name -> launch path
    schemes: dict[str, str] = field(default_factory=dict)     # "spotify" -> command that handles it
    clis: dict[str, str] = field(default_factory=dict)        # "code" -> full path
    scanned_at: float = 0.0

    def has_scheme(self, scheme: str) -> bool:
        return scheme.lower().rstrip(":") in self.schemes

    def has_cli(self, name: str) -> bool:
        return name.lower() in self.clis

    def app_named(self, fragment: str) -> Optional[str]:
        low = fragment.lower()
        for name in self.apps:
            if name.lower() == low:
                return name
        for name in self.apps:
            if low in name.lower():
                return name
        return None


_lock = threading.RLock()
_cache: Optional[Machine] = None

# The CLIs worth probing: each one is a quiet, scriptable way into an app.
KNOWN_CLIS = ("code", "code-insiders", "cursor", "windsurf", "gh", "git", "npm", "npx", "python",
              "winget", "docker", "wt", "pwsh", "powershell", "explorer", "notepad", "spotify")


def scan(force: bool = False) -> Machine:
    global _cache
    with _lock:
        if _cache is not None and not force and (time.monotonic() - _cache.scanned_at) < CACHE_S:
            return _cache
        m = Machine(scanned_at=time.monotonic())
        m.apps = _installed_apps()
        m.schemes = _uri_schemes()
        m.clis = {name: path for name in KNOWN_CLIS if (path := shutil.which(name))}
        _cache = m
        return m


def _installed_apps() -> dict[str, str]:
    """One walk of the Start Menu, not one per app: `find_app` re-walks it
    every call, which turned a scan into two seconds."""
    try:
        from agents.tools.app_tools import _is_junk, start_menu_dirs
    except Exception:  # noqa: BLE001 - not Windows, or tools missing
        return {}
    out: dict[str, str] = {}
    for root_dir in start_menu_dirs():
        for dirpath, _dirs, files in os.walk(root_dir):
            for f in files:
                if not f.lower().endswith((".lnk", ".url")):
                    continue
                stem = os.path.splitext(f)[0]
                if not _is_junk(stem):
                    out.setdefault(stem, os.path.join(dirpath, f))
    return out


def _uri_schemes() -> dict[str, str]:
    """Every registered `URL Protocol` in HKCR: the apps that can be driven
    by a link. This is what makes "open this playlist" one call instead of
    five clicks."""
    if not IS_WINDOWS:
        return {}
    try:
        import winreg
    except ImportError:
        return {}
    out: dict[str, str] = {}
    for root in (winreg.HKEY_CLASSES_ROOT, winreg.HKEY_CURRENT_USER):
        sub = r"" if root == winreg.HKEY_CLASSES_ROOT else r"Software\Classes"
        try:
            key = winreg.OpenKey(root, sub) if sub else winreg.ConnectRegistry(None, root)
            base = winreg.OpenKey(root, sub) if sub else winreg.OpenKey(root, "")
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                if ":" in name or "." in name or len(name) > 40:
                    continue
                low = name.lower()
                if low in out or low in _DULL:
                    continue
                try:
                    with winreg.OpenKey(base, name) as k:
                        winreg.QueryValueEx(k, "URL Protocol")
                        try:
                            with winreg.OpenKey(k, r"shell\open\command") as c:
                                command = winreg.QueryValueEx(c, "")[0]
                        except OSError:
                            command = ""
                except OSError:
                    continue
                out[low] = str(command)
        finally:
            try:
                base.Close()
                if sub:
                    key.Close()
            except Exception:  # noqa: BLE001
                pass
    return out


def open_uri(uri: str) -> None:
    """Hand a link to Windows - the app's own handler takes it from there.
    No window is focused and no key is pressed."""
    if not IS_WINDOWS:
        raise RuntimeError("deep links are a Windows path here")
    os.startfile(uri)  # noqa: S606 - a URI, handed to the registered handler


def run_cli(argv: list[str], *, timeout: float = 25.0, cwd: Optional[str] = None) -> tuple[int, str]:
    """Run an app's CLI without a console window. Returns (exit code, output)."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=cwd, creationflags=NO_WINDOW)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{argv[0]} is not on this machine") from exc
    except subprocess.TimeoutExpired:
        return -1, f"{argv[0]} did not finish within {timeout:g}s"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
