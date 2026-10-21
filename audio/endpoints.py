"""
Which speaker and microphone Windows is using right now.

19 Oct 2026, the owner: "I can't hear her." Headphones had become the
default after HeyTora started, and she was still talking into the laptop's
speakers: PortAudio takes its list of devices once, when it starts, and the
voice's output stream stays open for the whole run (HP-04). Windows' own
Core Audio says which endpoints are the default; `DeviceWatch` asks it every
couple of seconds (a fraction of a millisecond) and says when either changes,
so the app can reopen its audio there.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from typing import Callable, Optional

_log = logging.getLogger("tora.audio")

_E_RENDER, _E_CAPTURE = 0, 1
_E_MULTIMEDIA = 1


def _interfaces():
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], ctypes.POINTER(GUID), "iid"),
                      (["in"], ctypes.c_ulong, "ctx"), (["in"], ctypes.c_void_p, "params"),
                      (["out"], ctypes.POINTER(ctypes.c_void_p), "out")),
            COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], ctypes.c_ulong, "access"),
                      (["out"], ctypes.POINTER(ctypes.c_void_p), "out")),
            COMMETHOD([], HRESULT, "GetId", (["out"], ctypes.POINTER(ctypes.c_void_p), "id")),
            COMMETHOD([], HRESULT, "GetState", (["out"], ctypes.POINTER(ctypes.c_ulong), "state")),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], ctypes.c_uint, "flow"),
                      (["in"], ctypes.c_ulong, "mask"), (["out"], ctypes.POINTER(ctypes.c_void_p), "out")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], ctypes.c_uint, "flow"),
                      (["in"], ctypes.c_uint, "role"), (["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)), "out")),
        ]

    return GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"), IMMDeviceEnumerator


class Endpoints:
    """Windows' default speaker and microphone, by their endpoint ids.
    Use from one thread: COM is readied on the thread that makes it."""

    def __init__(self) -> None:
        import comtypes
        from comtypes import CLSCTX_ALL, CoCreateInstance
        comtypes.CoInitialize()
        clsid, enumerator = _interfaces()
        self._enum = CoCreateInstance(clsid, enumerator, CLSCTX_ALL)

    def _default(self, flow: int) -> str:
        try:
            device = self._enum.GetDefaultAudioEndpoint(flow, _E_MULTIMEDIA)
            ptr = device.GetId()
            try:
                return ctypes.wstring_at(ptr)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(ptr))
        except Exception:  # noqa: BLE001 - none of that kind plugged in
            return ""

    def defaults(self) -> tuple[str, str]:
        """(speaker id, microphone id) - "" for one there is none of."""
        return self._default(_E_RENDER), self._default(_E_CAPTURE)


class DeviceWatch:
    """Says, from its own thread, when Windows' default speaker or
    microphone has changed - (old, new) pairs of (speaker, microphone)."""

    EVERY_S = 2.0

    def __init__(self, on_change: Callable[[tuple, tuple], None]) -> None:
        self._on_change = on_change
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="audio-devices", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            endpoints = Endpoints()
        except Exception:  # noqa: BLE001 - not Windows, or no Core Audio: nothing to watch
            _log.debug("the audio device watch could not start", exc_info=True)
            return
        last = endpoints.defaults()
        while True:
            time.sleep(self.EVERY_S)
            now = endpoints.defaults()
            if now != last:
                try:
                    self._on_change(last, now)
                except Exception:  # noqa: BLE001 - a watcher must outlive its listeners
                    _log.debug("audio device change not handled", exc_info=True)
                last = now
