"""The system volume, as a number you can read and set.

Volume used to be N taps of a media key. That has three problems, and all
three showed up in the field: there is no way to know what the level
already is, no way to honour "set it to twenty percent", and no way to
tell afterwards whether anything actually happened — a key tap "succeeds"
whether or not the system was listening.

Core Audio answers all three. `IAudioEndpointVolume` gives the master
scalar (0.0–1.0) on the default output device, and it can be read as well
as written, which is what makes verification possible at all.

No new dependency: this is `comtypes`, already required by the pointer's
UI Automation path. pycaw would be the usual way and is one small wrapper
over exactly these three interfaces.

Everything here raises `Unavailable` rather than returning a wrong number.
A machine with no audio endpoint (a bare VM, RDP without audio) is a real
case, and "I could not read the volume" is a better answer than 0.
"""

from __future__ import annotations

import logging
import threading
from ctypes import POINTER, c_float, c_uint32
from ctypes.wintypes import BOOL, DWORD

from .model import Unavailable

log = logging.getLogger("agents.control")

_lock = threading.Lock()

# Render (output), and the role Windows uses for ordinary program audio.
_E_RENDER = 0
_E_MULTIMEDIA = 1
_CLSCTX_ALL = 23


def _interfaces():
    """The three COM interfaces, declared lazily.

    Lazily because importing comtypes costs ~80 ms and writes to its
    generated-code cache, and most sessions never touch the volume.
    """
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IAudioEndpointVolume(IUnknown):
        _iid_ = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
        # The vtable order is the contract: every method has to be declared,
        # in order, even the ones we never call.
        _methods_ = [
            COMMETHOD([], HRESULT, "RegisterControlChangeNotify",
                      (["in"], POINTER(IUnknown), "pNotify")),
            COMMETHOD([], HRESULT, "UnregisterControlChangeNotify",
                      (["in"], POINTER(IUnknown), "pNotify")),
            COMMETHOD([], HRESULT, "GetChannelCount",
                      (["out"], POINTER(c_uint32), "pnChannelCount")),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevel",
                      (["in"], c_float, "fLevelDB"), (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "SetMasterVolumeLevelScalar",
                      (["in"], c_float, "fLevel"), (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevel",
                      (["out"], POINTER(c_float), "pfLevelDB")),
            COMMETHOD([], HRESULT, "GetMasterVolumeLevelScalar",
                      (["out"], POINTER(c_float), "pfLevel")),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevel",
                      (["in"], c_uint32, "nChannel"), (["in"], c_float, "fLevelDB"),
                      (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "SetChannelVolumeLevelScalar",
                      (["in"], c_uint32, "nChannel"), (["in"], c_float, "fLevel"),
                      (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevel",
                      (["in"], c_uint32, "nChannel"), (["out"], POINTER(c_float), "pfLevelDB")),
            COMMETHOD([], HRESULT, "GetChannelVolumeLevelScalar",
                      (["in"], c_uint32, "nChannel"), (["out"], POINTER(c_float), "pfLevel")),
            COMMETHOD([], HRESULT, "SetMute",
                      (["in"], BOOL, "bMute"), (["in"], POINTER(GUID), "pguidEventContext")),
            COMMETHOD([], HRESULT, "GetMute",
                      (["out"], POINTER(BOOL), "pbMute")),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate",
                      (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "dwClsCtx"),
                      (["in"], POINTER(DWORD), "pActivationParams"),
                      (["out"], POINTER(POINTER(IAudioEndpointVolume)), "ppInterface")),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                      (["in"], DWORD, "dataFlow"), (["in"], DWORD, "dwStateMask"),
                      (["out"], POINTER(POINTER(IUnknown)), "ppDevices")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                      (["in"], DWORD, "dataFlow"), (["in"], DWORD, "role"),
                      (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
        ]

    return IMMDeviceEnumerator, IAudioEndpointVolume


def _endpoint():
    """The default output device's volume interface. Raises Unavailable."""
    try:
        import comtypes
        from comtypes import CoCreateInstance, GUID

        # Each thread that touches COM has to initialise it, and the control
        # actions run on the manager's asyncio thread, not the main one.
        try:
            comtypes.CoInitialize()
        except OSError:
            pass                      # already initialised on this thread
        enumerator_cls, volume_cls = _interfaces()
        enumerator = CoCreateInstance(
            GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),   # MMDeviceEnumerator
            enumerator_cls, _CLSCTX_ALL)
        device = enumerator.GetDefaultAudioEndpoint(_E_RENDER, _E_MULTIMEDIA)
        return device.Activate(volume_cls._iid_, _CLSCTX_ALL, None)
    except Unavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - no endpoint is a real machine state
        raise Unavailable(f"no audio device to read: {type(exc).__name__}: {exc}") from exc


def level() -> float:
    """Master output volume, 0.0–1.0. Raises Unavailable."""
    with _lock:
        return float(_endpoint().GetMasterVolumeLevelScalar())


def set_level(fraction: float) -> float:
    """Set the master volume and return what it actually became.

    Returning the READ-BACK value, not the requested one, is the point:
    some endpoints quantise, and a verification that trusts its own input
    verifies nothing.
    """
    target = max(0.0, min(1.0, float(fraction)))
    with _lock:
        endpoint = _endpoint()
        endpoint.SetMasterVolumeLevelScalar(c_float(target), None)
        return float(endpoint.GetMasterVolumeLevelScalar())


def muted() -> bool:
    with _lock:
        return bool(_endpoint().GetMute())


def set_muted(on: bool) -> bool:
    with _lock:
        endpoint = _endpoint()
        endpoint.SetMute(BOOL(1 if on else 0), None)
        return bool(endpoint.GetMute())


def available() -> bool:
    """Can this machine's volume be read at all?"""
    try:
        level()
        return True
    except Exception:  # noqa: BLE001
        return False


def describe(fraction: float) -> str:
    return f"{round(fraction * 100)} percent"
