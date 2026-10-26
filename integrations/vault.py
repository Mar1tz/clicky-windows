"""Where a connector's tokens live (CI-08).

In Windows Credential Manager, under "HeyTora/<provider>/<account>" - the
same store Windows uses for its own saved sign-ins, readable only by this
Windows user. Everything else in HeyTora holds only that name (the
reference), so no token is ever in a database, a log, a URL or a crash
report. Tests give it a dictionary instead (MemoryVault); the real one is
never touched by them.
"""
from __future__ import annotations

import ctypes
import json
from ctypes import wintypes
from typing import Optional

PREFIX = "HeyTora/"
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


def ref(provider: str, account: str) -> str:
    return f"{PREFIX}{provider}/{account.lower()}"


class _Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


class WindowsVault:
    """Windows Credential Manager (advapi32 CredWrite/CredRead/CredDelete)."""

    def put(self, target: str, secret: dict) -> None:
        blob = json.dumps(secret).encode("utf-8")
        buf = ctypes.create_string_buffer(blob, len(blob))
        cred = _Credential(Type=_CRED_TYPE_GENERIC, TargetName=target, CredentialBlobSize=len(blob),
                           CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)),
                           Persist=_CRED_PERSIST_LOCAL_MACHINE, UserName="HeyTora")
        if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(cred), 0):
            raise OSError(f"could not save to Windows Credential Manager ({ctypes.GetLastError()})")

    def get(self, target: str) -> Optional[dict]:
        p = ctypes.POINTER(_Credential)()
        if not ctypes.windll.advapi32.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
            return None
        try:
            c = p.contents
            raw = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
            return json.loads(raw.decode("utf-8"))
        finally:
            ctypes.windll.advapi32.CredFree(p)

    def delete(self, target: str) -> bool:
        return bool(ctypes.windll.advapi32.CredDeleteW(target, _CRED_TYPE_GENERIC, 0))


class MemoryVault:
    """A dictionary, for tests."""

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def put(self, target: str, secret: dict) -> None:
        self.items[target] = dict(secret)

    def get(self, target: str) -> Optional[dict]:
        return dict(self.items[target]) if target in self.items else None

    def delete(self, target: str) -> bool:
        return self.items.pop(target, None) is not None


_vault = None


def vault():
    """The vault for this process: Windows' own, unless a test set another."""
    global _vault
    if _vault is None:
        _vault = WindowsVault()
    return _vault


def use(v) -> None:
    global _vault
    _vault = v
