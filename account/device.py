"""This PC, as the referral rules see it: one free trial per PC, and a
friend on the referrer's own PC never counts (migration 0010,
docs/REFERRALS.md).

What goes to the server is a one-way hash of Windows' own machine ID
(HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid, set when Windows is
installed) - not the ID itself, nothing about the user, and nothing that
can be turned back into either. The same PC always gives the same hash;
two PCs never do. The privacy policy says so in one line.
"""
from __future__ import annotations

import hashlib
import sys
import uuid
from typing import Optional

SALT = "heytora-device-v1:"

_cached: Optional[str] = None


def _machine_guid() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0] or "")
    except OSError:
        return ""


def device_id() -> str:
    """This PC's one-way ID: 64 hex characters."""
    global _cached
    if _cached is None:
        raw = _machine_guid() or f"mac-{uuid.getnode():012x}"
        _cached = hashlib.sha256((SALT + raw.strip().lower()).encode("utf-8")).hexdigest()
    return _cached
