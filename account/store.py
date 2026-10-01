"""The signed-in session, kept on disk encrypted for this Windows user.

Windows' DPAPI (CryptProtectData) ties the file to the user's Windows login:
another account on the PC, or the file copied to another PC, cannot read it.
It holds the Supabase refresh token - enough to stay signed in, and nothing
of the business's: no Gemini key ever comes to this machine.
"""
from __future__ import annotations

import base64
import ctypes
import json
import logging
import sys
from pathlib import Path
from typing import Optional

_log = logging.getLogger("tora.account")
_ENTROPY = b"HeyTora account session"


def path() -> Path:
    from config import data_dir
    return data_dir() / "account.bin"


def load() -> Optional[dict]:
    p = path()
    if not p.exists():
        return None
    try:
        return json.loads(_unprotect(p.read_bytes()).decode("utf-8"))
    except Exception:  # noqa: BLE001 - another user's file, or damaged: signed out
        _log.warning("could not read the saved sign-in; signing out")
        return None


def save(session: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_protect(json.dumps(session).encode("utf-8")))


def clear() -> None:
    try:
        path().unlink()
    except FileNotFoundError:
        pass


# -- DPAPI -------------------------------------------------------------------------------------------------

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, object]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _protect(data: bytes) -> bytes:
    if sys.platform != "win32":                     # tests elsewhere; never shipped
        return b"plain:" + base64.b64encode(data)
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, _keep = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    if not crypt32.CryptProtectData(ctypes.byref(src), None, ctypes.byref(ent), None, None,
                                    0x1, ctypes.byref(out)):      # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError("could not encrypt the sign-in")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _unprotect(data: bytes) -> bytes:
    if data.startswith(b"plain:"):
        return base64.b64decode(data[6:])
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, _keep = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    if not crypt32.CryptUnprotectData(ctypes.byref(src), None, ctypes.byref(ent), None, None,
                                      0x1, ctypes.byref(out)):
        raise OSError("could not decrypt the sign-in")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)
