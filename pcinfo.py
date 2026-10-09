"""One line about the PC in tora.log, once a session (FS-09).

"On some people's PCs it's very slow" came with nothing to go on: no
numbers come back from users (SP-14 was declined), and a log that times
each step still cannot say that the machine has two cores, a busy CPU,
little free memory or three 4K monitors. This line can, from Windows' own
calls - no extra package.
"""
from __future__ import annotations

import ctypes
import os
import platform
import time
from ctypes import wintypes


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _memory() -> str:
    m = _MemoryStatus()
    m.dwLength = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return "RAM ?"
    return f"RAM {m.ullTotalPhys / 2**30:.0f} GB ({m.ullAvailPhys / 2**30:.1f} GB free)"


def _cpu_busy(sample_s: float = 0.25) -> str:
    """How busy the CPU is, over a quarter of a second."""
    def times():
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        as_int = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime   # noqa: E731
        return as_int(idle), as_int(kernel) + as_int(user)       # kernel time includes idle
    i0, t0 = times()
    time.sleep(sample_s)
    i1, t1 = times()
    total = t1 - t0
    return f"CPU {100 * (1 - (i1 - i0) / total):.0f}% busy" if total > 0 else "CPU ?"


def _monitors() -> str:
    try:
        from screen import monitors
        mons = monitors.monitors()
        each = ", ".join(f"{m.width}x{m.height}@{round((m.scale or 1) * 100)}%" for m in mons)
        return f"{len(mons)} monitor(s): {each}"
    except Exception:  # noqa: BLE001
        return "monitors ?"


def describe() -> str:
    """'pc: 8 cores, CPU 12% busy, RAM 16 GB (6.2 GB free), 3 monitor(s): ..., Windows 10.0.26200'"""
    parts = [f"{os.cpu_count() or '?'} cores"]
    for part in (_cpu_busy, _memory, _monitors):
        try:
            parts.append(part())
        except Exception:  # noqa: BLE001 - one missing number must not lose the rest
            parts.append("?")
    parts.append(f"Windows {platform.version()}")
    return "pc: " + ", ".join(parts)
