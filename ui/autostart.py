"""Start HeyTora when Windows starts (APP-11).

The app window's Settings page offered this as a switch that did nothing.
It is the standard per-user way: a value named "HeyTora" under

    HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

holding the command that starts HeyTora - the exe when packaged, pythonw and
main.py from source (pythonw, so no console window opens at sign-in).
Per-user: no admin rights, and nothing for anyone else on the machine.

The registry is reached only through _read_value/_write_value/_delete_value,
so tests replace those three and never touch the real one.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_log = logging.getLogger("tora.autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "HeyTora"
# The name before the rename. A "start with Windows" turned on as Tora is
# carried over once (migrate_legacy): otherwise the switch would read off
# while the old entry went on starting the old exe at every sign-in.
LEGACY_VALUE = "Tora"


def command() -> str:
    """What Windows runs at sign-in to start this HeyTora."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    runner = windowless if windowless.exists() else exe
    main = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{runner}" "{main}"'


def enabled() -> bool:
    """Is HeyTora set to start with Windows - as THIS HeyTora?"""
    try:
        return _read_value() == command()
    except Exception:  # noqa: BLE001 - no registry, no autostart
        return False


def set_enabled(on: bool) -> bool:
    """Turn starting with Windows on or off. True if it took."""
    try:
        if on:
            _write_value(command())
        else:
            _delete_value()
        return enabled() == bool(on)
    except Exception:  # noqa: BLE001
        _log.warning("could not change starting with Windows", exc_info=True)
        return False


def migrate_legacy() -> bool:
    """Carry a "start with Windows" set under the old name over to HeyTora.
    Runs once at start-up; True if there was one to carry."""
    try:
        if not _read_value(LEGACY_VALUE):
            return False
        if not _read_value():
            _write_value(command())
        _delete_value(LEGACY_VALUE)
        _log.info("start with Windows carried over from %r to %r", LEGACY_VALUE, VALUE)
        return True
    except Exception:  # noqa: BLE001 - never stop the app starting
        _log.debug("could not carry the old start-with-Windows entry over", exc_info=True)
        return False


# ── the registry, and nothing else ───────────────────────────────────────────

def _read_value(name: str = VALUE) -> str:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, name)
            return str(value)
    except FileNotFoundError:
        return ""


def _write_value(value: str, name: str = VALUE) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_value(name: str = VALUE) -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass


if os.name != "nt":                         # pragma: no cover - Windows app
    def _read_value(name: str = VALUE) -> str:   # noqa: F811
        return ""
