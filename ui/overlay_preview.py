"""See every state of the overlay - no voice, no model, no question needed.

    python -m ui.overlay_preview                     play every state on screen, new look
    python -m ui.overlay_preview --style classic     today's look
    python -m ui.overlay_preview --loop              keep playing
    python -m ui.overlay_preview --shots DIR         write a PNG of every state instead of showing
                                                     anything: both looks, on a light, a dark and a
                                                     busy backdrop
    python -m ui.overlay_preview --place right --size large --reduced
                                                     the new look with Settings > On screen's
                                                     choices (OD-08); they work with --shots too

The redesign (milestones OD-01..09) is reviewed here before it reaches a real
walkthrough: every island state, every cursor state, the step label at the
screen's edge and in a corner. The pointer is scripted, so the cursor sits
where each scene needs it rather than wherever the real mouse is, and the
screenshots come out the same on every run.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QApplication

BACKDROPS = ("light", "dark", "busy")
LONG_ANSWER = ("The export button is in the File menu, second item from the top. Choose it, then pick where "
               "the file should go and press Save.")


@dataclass
class Scene:
    name: str
    says: str                                  # what the scene shows, for the console
    apply: Callable[["object", "Stage"], None]
    settle: float = 1.0                        # seconds for its animation to finish
    target: Optional[tuple[float, float, str]] = None   # a pretend control on the backdrop


class Stage:
    """The screen the preview plays on, and the scripted pointer."""

    def __init__(self, rect: QRectF) -> None:
        self.r = QRectF(rect)
        self.mouse = QPointF(self.r.center().x() - 160, self.r.center().y() + 60)

    def at(self, dx: float, dy: float) -> tuple[float, float]:
        return self.r.center().x() + dx, self.r.center().y() + dy


def scenes(stage: Stage) -> list[Scene]:
    ex = stage.at(90, -10)
    edge = (stage.r.right() - 30, stage.r.center().y() + 40)
    corner = (stage.r.right() - 22, stage.r.top() + 18)
    save = stage.at(-240, -70)

    def speak(ov, text: str) -> None:
        ov.caption_state("SPEAKING")
        for word in text.split(" "):
            ov.caption_chunk(word + " ")
        ov.caption_done()

    def target(ov, xy: tuple[float, float]) -> None:
        # What the app sends after pointing when Windows knows the control's
        # bounds (CompanionManager._highlight): here, the pretend button's.
        ov.add_shape({"kind": "rect", "color": "blue", "role": "target", "x1": xy[0] - 49, "y1": xy[1] - 18,
                      "x2": xy[0] + 49, "y2": xy[1] + 18})

    def state(ov, name: str) -> None:
        # What main.py does on every state change: the cursor and the island.
        ov.set_mode(name.lower())
        ov.caption_state(name)

    def step(ov, n: int, total: int, instruction: str, xy: tuple[float, float], label: str) -> None:
        ov.set_guide_progress(n, total, instruction)
        ov.guide_point(xy[0], xy[1], label)
        target(ov, xy)

    return [
        Scene("idle", "resting beside the pointer", lambda ov, s: None, 0.8),
        Scene("listening", "listening to you", lambda ov, s: (state(ov, "LISTENING"),
                                                             [ov.set_audio_level(0.2) for _ in range(6)]), 0.8),
        Scene("thinking", "working on it", lambda ov, s: (state(ov, "THINKING"),
                                                         ov.set_activity("finding", "Looking for the Export button")), 1.0),
        Scene("speaking-short", "a one-line answer", lambda ov, s: speak(ov, "Sure - it's in the File menu."), 2.2),
        Scene("speaking-long", "a long answer", lambda ov, s: speak(ov, LONG_ANSWER), 4.5),
        Scene("pointing", "pointing while explaining", lambda ov, s: (ov.point_at(ex[0], ex[1], "Export"),
                                                                    target(ov, ex)), 2.4,
              (ex[0], ex[1], "Export")),
        Scene("guide-step", "a walkthrough step", lambda ov, s: step(ov, 2, 5, "Click Export.", ex, "Export"), 0.9,
              (ex[0], ex[1], "Export")),
        Scene("guide-keys", "a step that is a shortcut", lambda ov, s: step(ov, 4, 5, "Press Ctrl+S to save.", save,
                                                                          "Save"), 0.9, (save[0], save[1], "Save")),
        Scene("guide-edge", "a target at the screen's right edge", lambda ov, s: step(ov, 3, 5, "Open Settings.", edge,
                                                                                    "Settings"), 0.9,
              (edge[0], edge[1], "Settings")),
        Scene("guide-corner", "a target in the top-right corner", lambda ov, s: step(ov, 5, 5, "Close the window.",
                                                                                   corner, "Close"), 0.9,
              (corner[0], corner[1], "Close")),
        Scene("guide-done", "the walkthrough is over", lambda ov, s: (ov.set_guide_progress(0, 0, ""),
                                                                     ov.guide_release()), 1.8),
    ]


REQUIRED = {"idle", "listening", "thinking", "speaking-short", "speaking-long", "pointing", "guide-step",
            "guide-keys", "guide-edge", "guide-corner", "guide-done"}


# -- driving the real overlay ---------------------------------------------------------------------------

def attach(overlay, stage: Stage) -> None:
    """Script this overlay's pointer and pin its caption to the stage's screen.
    Only this instance changes; the real pointer is never touched."""
    overlay._pointer_pos = lambda: QPoint(int(stage.mouse.x()), int(stage.mouse.y()))
    overlay._caption_screen_rect = lambda: QRectF(stage.r)
    overlay._display_pos = QPointF(stage.mouse.x() + 20, stage.mouse.y() + 16)


def reset(overlay) -> None:
    overlay.set_guide_progress(0, 0, "")
    overlay.guide_release()
    overlay._release_lock()
    overlay.set_activity("", "")
    overlay.set_mode("idle")
    overlay.caption_state("IDLE")
    overlay._caption.clear()
    overlay.clear_annotations()


def pump(seconds: float) -> None:
    app = QApplication.instance()
    end = time.monotonic() + max(0.0, seconds)
    while True:
        app.processEvents()
        if time.monotonic() >= end:
            break
        time.sleep(0.005)


def play(overlay, stage: Stage, scene: Scene, settle: Optional[float] = None) -> None:
    reset(overlay)
    pump(0.05)
    scene.apply(overlay, stage)
    pump(scene.settle if settle is None else settle)


# -- screenshots ----------------------------------------------------------------------------------------

def _paint_backdrop(p: QPainter, rect: QRect, kind: str) -> None:
    if kind == "busy":
        g = QLinearGradient(0, rect.top(), rect.width(), rect.bottom())
        g.setColorAt(0.0, QColor("#2B5876"))
        g.setColorAt(0.5, QColor("#C06C84"))
        g.setColorAt(1.0, QColor("#F8B195"))
        p.fillRect(rect, QBrush(g))
        p.setPen(QPen(QColor(255, 255, 255, 60), 1))
        for y in range(rect.top() + 12, rect.bottom(), 18):
            p.drawLine(rect.left(), y, rect.right(), y + 30)
        return
    bg, bar, line = ("#F3F3F3", "#E1E1E1", "#C9C9C9") if kind == "light" else ("#1F1F1F", "#2D2D2D", "#3A3A3A")
    p.fillRect(rect, QColor(bg))
    p.fillRect(QRect(rect.left(), rect.top(), rect.width(), 22), QColor(bar))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(line))
    for i, y in enumerate(range(rect.top() + 40, rect.bottom() - 10, 22)):
        p.drawRoundedRect(QRectF(rect.left() + 24, y, 160 + (i * 53) % 260, 8), 4, 4)


def _paint_control(p: QPainter, centre: QPointF, label: str, kind: str) -> None:
    fill, edge, ink = (("#FFFFFF", "#BDBDBD", "#1F1F1F") if kind != "dark" else ("#3A3A3A", "#555555", "#F0F0F0"))
    r = QRectF(centre.x() - 45, centre.y() - 14, 90, 28)
    p.setPen(QPen(QColor(edge), 1))
    p.setBrush(QColor(fill))
    p.drawRoundedRect(r, 5, 5)
    f = QFont("Segoe UI")
    f.setPixelSize(12)
    p.setFont(f)
    p.setPen(QColor(ink))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, label)


def _crop(stage: Stage, focus: QPointF, w: int, h: int) -> QRect:
    x = int(min(max(focus.x() - w / 2, stage.r.left()), stage.r.right() - w))
    y = int(min(max(focus.y() - h / 2, stage.r.top()), stage.r.bottom() - h))
    return QRect(x, y, w, h)


def setup(overlay, style: str, place: str = "centre", size: str = "normal", reduced: bool = False) -> None:
    """The look, and Settings > On screen's choices, as main.py applies them."""
    overlay.set_style(style)
    overlay.set_island(place, size)
    overlay.set_reduced_motion(reduced)


def capture(overlay, stage: Stage, scene: Scene, kind: str, style: str) -> QImage:
    """The island strip on top - wherever the island sits - and the area round
    the cursor below, over one backdrop. In the screen's own pixels, so a shot
    at 150% scaling shows what 150% really looks like."""
    slot = overlay._caption.slot(stage.r)
    mid = slot.center().x() if slot is not None else stage.r.center().x()
    left = min(max(mid - 320, stage.r.left()), stage.r.right() - 640)
    island = QRect(int(left), int(stage.r.top()), 640, 110)
    focus = overlay._locked_pos if overlay._locked_pos is not None else overlay._display_pos
    near = _crop(stage, QPointF(focus), 640, 300)
    head, gap = 20, 8
    dpr = max(1.0, overlay.devicePixelRatioF())
    img = QImage(round(640 * dpr), round((head + island.height() + gap + near.height()) * dpr),
                 QImage.Format.Format_ARGB32)
    img.setDevicePixelRatio(dpr)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    f = QFont("Segoe UI")
    f.setPixelSize(11)
    p.setFont(f)
    p.setPen(QColor("#444444"))
    p.drawText(QRect(6, 0, 628, head), Qt.AlignmentFlag.AlignVCenter,
               f"{scene.name} - {scene.says} - {style} look, {kind} backdrop")
    for crop, top in ((island, head), (near, head + island.height() + gap)):
        dest = QRect(0, top, crop.width(), crop.height())
        p.save()
        p.setClipRect(dest)
        p.translate(dest.left() - crop.left(), dest.top() - crop.top())
        _paint_backdrop(p, crop, kind)
        if scene.target is not None:
            _paint_control(p, QPointF(scene.target[0], scene.target[1]), scene.target[2], kind)
        local = crop.translated(-overlay.x(), -overlay.y())
        p.drawPixmap(crop.topLeft(), overlay.grab(local))
        if crop.right() >= int(stage.r.right()) - 1:          # mark the screen's edge
            p.setPen(QPen(QColor("#E53935"), 2, Qt.PenStyle.DashLine))
            p.drawLine(crop.right(), crop.top(), crop.right(), crop.bottom())
        p.restore()
    p.end()
    return img


def shots(out_dir: str, styles: tuple[str, ...] = ("classic", "new"), settle_scale: float = 1.0,
          only: Optional[set[str]] = None, **look) -> list[str]:
    """PNGs of every scene (or `only` these), in each look; `look` is
    Settings > On screen's place=, size=, reduced=."""
    from ui.overlay import CursorOverlay
    app = QApplication.instance() or QApplication(sys.argv[:1])
    os.makedirs(out_dir, exist_ok=True)
    screen = app.primaryScreen()
    stage = Stage(QRectF(screen.availableGeometry()))
    written = []
    for style in styles:
        ov = CursorOverlay()
        attach(ov, stage)
        setup(ov, style, **look)
        try:
            for i, sc in enumerate(scenes(stage), 1):
                if only is not None and sc.name not in only:
                    continue
                play(ov, stage, sc, sc.settle * settle_scale)
                for kind in BACKDROPS:
                    path = os.path.join(out_dir, f"{style}-{i:02d}-{sc.name}-{kind}.png")
                    capture(ov, stage, sc, kind, style).save(path)
                    written.append(path)
        finally:
            ov._tick_timer.stop()
            ov.deleteLater()
    return written


def live(style: str, loop: bool, **look) -> int:
    from ui.overlay import CursorOverlay
    app = QApplication.instance() or QApplication(sys.argv[:1])
    screen = app.primaryScreen()
    stage = Stage(QRectF(screen.availableGeometry()))
    ov = CursorOverlay()
    attach(ov, stage)
    setup(ov, style, **look)
    ov.show()
    order = scenes(stage)
    state = {"i": 0}

    def next_scene() -> None:
        if state["i"] >= len(order):
            if not loop:
                app.quit()
                return
            state["i"] = 0
        sc = order[state["i"]]
        state["i"] += 1
        print(f"{sc.name:15} {sc.says}", flush=True)
        reset(ov)
        sc.apply(ov, stage)
        QTimer.singleShot(int((sc.settle + 1.2) * 1000), next_scene)

    QTimer.singleShot(300, next_scene)
    return app.exec()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ui.overlay_preview", description=__doc__.split("\n\n")[0])
    ap.add_argument("--style", choices=["new", "classic", "both"], default="new")
    ap.add_argument("--shots", metavar="DIR", help="write PNGs of every state instead of showing them")
    ap.add_argument("--loop", action="store_true", help="keep playing")
    # Settings > On screen (OD-08); the new look only.
    ap.add_argument("--place", choices=["centre", "right", "off"], default="centre", help="where the island sits")
    ap.add_argument("--size", choices=["small", "normal", "large"], default="normal", help="the island's size")
    ap.add_argument("--reduced", action="store_true", help="reduced motion")
    args = ap.parse_args(argv)
    look = {"place": args.place, "size": args.size, "reduced": args.reduced}
    if args.shots:
        styles = ("classic", "new") if args.style == "both" else (args.style,)
        paths = shots(args.shots, styles, **look)
        print(f"{len(paths)} screenshots in {os.path.abspath(args.shots)}")
        return 0
    return live("new" if args.style == "both" else args.style, args.loop, **look)


if __name__ == "__main__":
    sys.exit(main())
