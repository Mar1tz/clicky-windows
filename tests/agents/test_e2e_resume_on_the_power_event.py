"""ST-06: the mic and the voice come back when Windows says it has woken.

A heartbeat thread noticed a resume up to 5 s late and waited a second more.
Windows sends every top-level window WM_POWERBROADCAST on resume; main.py's
filter hears it, and the heartbeat stays as the backstop - once either way.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import companion_manager as cm


def test_windows_resume_message_reopens_the_mic():
    import main
    woke = []
    f = main._PowerEvents(lambda: woke.append(1))
    msg = wintypes.MSG(None, 0x0218, 0x0012, 0)                 # PBT_APMRESUMEAUTOMATIC
    assert f.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg)) == (False, 0)
    other = wintypes.MSG(None, 0x0218, 0x0004, 0)               # PBT_APMSUSPEND: going to sleep
    f.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(other))
    assert woke == [1]


def test_a_resume_is_handled_once_whoever_notices_it(monkeypatch):
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    ran = []
    m._on_system_resume = lambda: ran.append(1)
    started = []
    monkeypatch.setattr(cm.threading, "Thread",
                        lambda target, **kw: SimpleNamespace(start=lambda: (started.append(1), target())))
    m.on_power_resume()                                          # Windows' message
    m.on_power_resume()                                          # the heartbeat, a few seconds later
    assert ran == [1]
