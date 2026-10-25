"""
Multi-monitor screenshot helper.

The tricky part is keeping THREE coordinate systems straight:

  1. PHYSICAL  — what mss.grab() returns. Real GPU pixels. e.g. 2560x1440.
  2. LOGICAL   — what the OS / Qt cursor uses after DPI scaling. e.g. 1707x960
                 on a 150%-DPI 2560x1440 display.
  3. DOWNSCALED — what we send to the LLM (1280-wide JPEG to keep tokens low).

The overlay plots in LOGICAL coordinates. The element-detector model sees the
downscaled image. So `detect_element` must return coords in LOGICAL space, with
the monitor's logical-origin offset applied for multi-monitor setups.

ScreenShot now carries every number needed to convert between them.
"""

import base64
import ctypes
import io
from dataclasses import dataclass
from typing import List

import mss
import mss.tools

import capture_watch
from PIL import Image


@dataclass
class ScreenShot:
    index: int

    # Downscaled image actually sent to the LLM
    width: int            # downscaled width (pixels in JPEG)
    height: int           # downscaled height
    base64_jpeg: str

    # Real (physical) monitor size and origin in mss virtual-screen coords
    physical_width: int
    physical_height: int
    physical_left: int    # mss-reported origin (physical px)
    physical_top: int

    # THIS monitor's scaling (physical / logical): 1.0 at 100%, 1.25 at 125%.
    dpi_scale: float

    # Where this monitor's top-left sits in LOGICAL screen space - as Qt has
    # it, which is where Windows has it (see screen/monitors.py), not the
    # physical position divided by a scaling.
    logical_left: int
    logical_top: int


def _query_dpi_scale() -> float:
    """The system scaling - the main monitor's at sign-in. Only a fallback:
    each monitor has its own (screen.monitors), and dividing every monitor
    by this one put Tora's arrows on the wrong spot, or the wrong monitor,
    anywhere but the main screen. Returns 1.0 if anything goes wrong.

    It no longer calls SetProcessDPIAware: Qt makes the process per-monitor
    DPI aware, and that call would downgrade a process that was not yet."""
    try:
        gdfs = getattr(ctypes.windll.user32, "GetDpiForSystem", None)
        if gdfs:
            return max(1.0, gdfs() / 96.0)
        return 1.0
    except Exception:
        return 1.0


def _placement(left: int, top: int, mons) -> tuple[float, int, int]:
    """(scaling, logical left, logical top) of the monitor whose physical
    top-left corner is (left, top) - its own scaling, not the system's."""
    for m in mons:
        if m.left == left and m.top == top:
            return m.scale or 1.0, int(round(m.logical[0])), int(round(m.logical[1]))
    dpi = _query_dpi_scale()
    return dpi, int(round(left / dpi)), int(round(top / dpi))


def monitor_frames() -> List[ScreenShot]:
    """Every monitor's numbers - where it is, its size and scaling - with no
    picture (base64_jpeg ""). FS-05: a look needs ONE picture; grabbing and
    encoding every monitor to use one of them took 331 ms with three
    monitors on 9 Oct, before the window was grabbed again anyway."""
    from screen import monitors
    mons = monitors.monitors()
    out = []
    with mss.mss() as sct:
        for i, m in enumerate(sct.monitors[1:], start=1):
            left, top = int(m.get("left", 0)), int(m.get("top", 0))
            dpi, log_left, log_top = _placement(left, top, mons)
            out.append(ScreenShot(index=i, width=int(m["width"]), height=int(m["height"]), base64_jpeg="",
                                  physical_width=int(m["width"]), physical_height=int(m["height"]),
                                  physical_left=left, physical_top=top, dpi_scale=dpi,
                                  logical_left=log_left, logical_top=log_top))
    return out


def grab_frame(frame: ScreenShot, max_width: int = 1280):
    """The same monitor with its picture: one grab, one JPEG. None on failure."""
    got = capture_region(frame.physical_left, frame.physical_top, frame.physical_width,
                         frame.physical_height, max_width)
    if got is None:
        return None
    b64, w, h = got
    from dataclasses import replace
    return replace(frame, base64_jpeg=b64, width=w, height=h)


def capture_all_screens(max_width: int = 1280) -> List[ScreenShot]:
    """Capture all monitors. Each ScreenShot carries everything needed
    to convert detection coords back into logical screen space."""
    from screen import monitors
    mons = monitors.monitors()
    results = []
    with mss.mss() as sct:
        # mss monitor index 0 is the combined virtual screen; 1+ are real monitors
        for i, monitor in enumerate(sct.monitors[1:], start=1):
            raw = sct.grab(monitor)
            capture_watch.screen()
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

            phys_w, phys_h = img.width, img.height
            phys_left = int(monitor.get("left", 0))
            phys_top  = int(monitor.get("top",  0))
            dpi, log_left, log_top = _placement(phys_left, phys_top, mons)

            # Downscale only the JPEG we send to the LLM — keep physical numbers intact
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize(
                    (max_width, int(img.height * ratio)),
                    Image.Resampling.LANCZOS,
                )

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75, optimize=True)
            encoded = base64.b64encode(buf.getvalue()).decode("utf-8")

            results.append(ScreenShot(
                index=i,
                width=img.width,
                height=img.height,
                base64_jpeg=encoded,
                physical_width=phys_w,
                physical_height=phys_h,
                physical_left=phys_left,
                physical_top=phys_top,
                dpi_scale=dpi,
                logical_left=log_left,
                logical_top=log_top,
            ))

    return results


def capture_region(left: int, top: int, width: int, height: int, max_width: int = 1280):
    """(base64 JPEG, its width, its height) of a PHYSICAL-pixel region - the
    window the user is working in (UD-02). Grabbed at full resolution and
    then downscaled, so a window that is a third of the monitor reaches the
    model three times sharper than in a whole-screen shot. None on failure."""
    if width < 16 or height < 16:
        return None
    try:
        with mss.mss() as sct:
            raw = sct.grab({"left": int(left), "top": int(top), "width": int(width), "height": int(height)})
        capture_watch.screen()
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except Exception:
        return None
    if img.width > max_width:
        img = img.resize((max_width, max(1, int(img.height * max_width / img.width))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("utf-8"), img.width, img.height


def capture_primary(max_width: int = 1280) -> ScreenShot:
    """The main monitor's screenshot. Not screens[0]: mss lists monitors in
    its own order, and with three it was the right-hand one. The main
    monitor is the one whose top-left corner is the desktop's origin. Only
    it is grabbed (as looks do since FS-05): every monitor was grabbed and
    encoded to keep one."""
    frames = monitor_frames()
    if not frames:
        return None
    frame = next((s for s in frames if s.physical_left == 0 and s.physical_top == 0), frames[0])
    return grab_frame(frame, max_width)


def screen_count() -> int:
    with mss.mss() as sct:
        return len(sct.monitors) - 1  # subtract virtual combined monitor
