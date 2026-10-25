"""The calibration probe (PT-09, the Massive Direction Plan).

Does a mark drawn by the overlay land on the pixel the capture sees? For
each monitor a small magenta crosshair is drawn at a known LOGICAL point -
the way Tora's marks are drawn - the monitor is grabbed, the crosshair is
found in the picture, and the picture's pixel is converted back through
screen/monitors.py. The distance between where it was meant to be and
where the capture saw it is the error; a sound layout is within a pixel
or two.

    python -m screen.calibrate        # each monitor, under a second each

find_mark() and error() are pure, so the arithmetic is tested at 100, 125
and 150% and mixed scaling, with monitors left of and above the main one
(tests/agents/test_e2e_calibration.py); run() needs a screen.
"""
from __future__ import annotations

import math
from typing import Optional

from screen import monitors
from screen.monitors import Frame, Logical, Monitor, Phys

MARK = (255, 0, 255)               # nothing on a normal screen is this colour
ARM_LOGICAL = 14                   # the crosshair's half-length, logical px
TOLERANCE = 60                     # per channel


def find_mark(image, color: tuple = MARK, tol: int = TOLERANCE) -> Optional[tuple[float, float]]:
    """The centre of the crosshair in a PIL image, in its own pixels, or None."""
    import numpy as np
    a = np.asarray(image.convert("RGB")).astype(int)
    hit = (abs(a[..., 0] - color[0]) < tol) & (abs(a[..., 1] - color[1]) < tol) & (abs(a[..., 2] - color[2]) < tol)
    ys, xs = np.nonzero(hit)
    if len(xs) < 4:
        return None
    # The arms cross at the median row and column of the marked pixels.
    return float(np.median(xs)), float(np.median(ys))


def error(meant: Logical, seen_in_picture: tuple[float, float], frame: Frame,
          mons: Optional[list[Monitor]] = None) -> float:
    """Logical px between where the mark was meant and where the capture saw it."""
    seen = frame.logical(frame.phys(*seen_in_picture), mons)
    return math.hypot(seen.x - meant.x, seen.y - meant.y)


def targets(m: Monitor) -> list[Logical]:
    """Where to put the crosshair on a monitor: its middle and two corners in."""
    lx, ly, lw, lh = m.logical
    return [Logical(lx + lw * fx, ly + lh * fy) for fx, fy in ((0.5, 0.5), (0.2, 0.25), (0.8, 0.75))]


def run(max_error: float = 2.0) -> list[tuple[str, float]]:
    """Draw, grab and compare on every monitor. [(monitor, worst error px)]."""
    import mss
    from PIL import Image
    from PyQt6.QtCore import QEventLoop, QRectF, Qt, QTimer
    from PyQt6.QtGui import QColor, QPainter, QPen
    from PyQt6.QtWidgets import QApplication, QWidget

    import capture_watch

    app = QApplication.instance() or QApplication([])
    monitors.publish([(g.x(), g.y(), g.width(), g.height())
                      for g in (s.geometry() for s in app.screens())])
    out = []
    for m in monitors.monitors():
        worst = 0.0
        for at in targets(m):
            w = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                        | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
            w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            w.setGeometry(int(at.x) - ARM_LOGICAL, int(at.y) - ARM_LOGICAL, 2 * ARM_LOGICAL + 1, 2 * ARM_LOGICAL + 1)

            def paint(_e, w=w):
                p = QPainter(w)
                p.setPen(QPen(QColor(*MARK), 3))
                c = ARM_LOGICAL
                p.drawLine(0, c, 2 * c, c)
                p.drawLine(c, 0, c, 2 * c)
                p.end()
            w.paintEvent = paint
            w.show()
            loop = QEventLoop()
            QTimer.singleShot(250, loop.quit)
            loop.exec()
            with mss.mss() as sct:
                raw = sct.grab({"left": m.left, "top": m.top, "width": m.width, "height": m.height})
            capture_watch.screen()
            w.close()
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            frame = Frame(m.left, m.top, m.width, m.height, img.width, img.height)
            seen = find_mark(img)
            worst = max(worst, error(at, seen, frame) if seen else float("inf"))
        out.append((m.name, worst))
    return out


if __name__ == "__main__":
    for name, worst in run():
        print(f"{name}: worst {worst:.1f} px" + ("  OK" if worst <= 2.0 else "  OFF"))
