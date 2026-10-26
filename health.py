"""The health panel's checks (EN-17, the Massive Direction Plan).

Settings > Advanced already said whether the microphone, the live session,
the workforce, the screens and OCR work. These are the rest of "is
HeyTora healthy": whether Windows lets apps use the microphone at all, the
code-editor connector and the account, how much disk is left where HeyTora
keeps its data, the version of each database, and the last update. Each is
(name, detail, "ok" | "warn" | "bad"); all local, nothing here goes online.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path

Row = tuple[str, str, str]
_MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def _consent(sub: str = "") -> str:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MIC_KEY + ("\\" + sub if sub else "")) as key:
        return str(winreg.QueryValueEx(key, "Value")[0])


def mic_permission() -> Row:
    """Windows' own switch: Settings > Privacy > Microphone, for every app
    and for desktop apps like HeyTora."""
    try:
        everyone = _consent()
        desktop = _consent("NonPackaged")
    except Exception:  # noqa: BLE001 - not set is allowed
        return ("Microphone permission", "Allowed", "ok")
    if everyone.lower() == "deny":
        return ("Microphone permission", "Off for every app in Windows privacy settings", "bad")
    if desktop.lower() == "deny":
        return ("Microphone permission", "Off for desktop apps in Windows privacy settings", "bad")
    return ("Microphone permission", "Allowed", "ok")


def disk(folder: Path) -> Row:
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return ("Disk space", "Could not read", "warn")
    gb = free / 1e9
    level = "bad" if gb < 0.5 else "warn" if gb < 2 else "ok"
    return ("Disk space", f"{gb:.1f} GB free where HeyTora keeps its data", level)


def databases(folder: Path) -> list[Row]:
    rows = []
    for path in sorted(folder.glob("*.db")):
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
            try:
                version = conn.execute("pragma user_version").fetchone()[0]
                tables = conn.execute("select count(*) from sqlite_master where type='table'").fetchone()[0]
            finally:
                conn.close()
            size = path.stat().st_size / 1e6
            rows.append((f"Database {path.name}", f"v{version} · {tables} tables · {size:.1f} MB", "ok"))
        except sqlite3.Error as exc:
            rows.append((f"Database {path.name}", f"Will not open: {exc}", "bad"))
    return rows


def connectors() -> list[Row]:
    rows = []
    try:
        from devlink import current
        hub = current()
        if hub is None:
            rows.append(("Code editors (DevLink)", "Off", "warn"))
        else:
            n = len([c for c in hub.gateway.connections if not getattr(c, "closed", False)])
            rows.append(("Code editors (DevLink)", f"Running · {n} editor window(s) connected", "ok"))
    except Exception:  # noqa: BLE001
        pass
    try:
        from account.service import account
        a = account()
        rows.append(("HeyTora account", "Signed in" if a.signed_in() else "Signed out",
                     "ok" if a.signed_in() else "warn"))
    except Exception:  # noqa: BLE001
        pass
    return rows


def last_update() -> Row:
    try:
        from updates.service import updater
        st = updater().state()
    except Exception:  # noqa: BLE001
        return ("Updates", "Unknown", "warn")
    if st["state"] == "dev":
        return ("Updates", f"{st['current']} · running from source, not updated in place", "ok")
    checked = st.get("checked_at") or 0
    when = "never checked" if not checked else f"checked {_ago(time.time() - checked)}"
    newer = f" · {st['latest']} is out" if st.get("latest") and st["latest"] != st["current"] else ""
    level = "warn" if newer or not checked else "ok"
    return ("Updates", f"{st['current']} · {when}{newer}", level)


def _ago(seconds: float) -> str:
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} days ago"


def rows(folder: Path) -> list[Row]:
    return [mic_permission(), disk(folder), *databases(folder), *connectors(), last_update()]
