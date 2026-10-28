"""The shape: HeyTora's one home, grown out of the pill (DESIGN.md §1-§3).

A click on the resting pill (ui/flow_bar.py) grows it, beside the pill and
towards the middle of the screen, into a small app: tabs along its top,
Upgrade and Settings on the right, the page under them. Escape, a click
somewhere else, or the pill again shrinks it back. Closing never stops a job.

    pill ( ● )  ->  ┌ [Home][Insights][Jobs] ········ [Upgrade][⚙] ┐
                    │                 the page                     │
                    └──────────────────────────────────────────────┘

The window is made at the full size once and stays still; what grows is the
shape painted inside it, from the pill's own rectangle to the whole window,
in the pill's black turning into the theme's ground. Resizing a Windows
window every frame flickers; painting does not. The page fades in over the
last part, lifted 6 px into place.

Step 1 of the revamp (DESIGN.md §13): Home is the shape's own
(ui/shape_pages.py). The app window's pages are built for a 1040 px column
and cannot be squeezed into 520, so until each has its shape-sized page,
its tab - Insights, Jobs - and the gear, Upgrade and Invite & earn open the
app window there (`open_app`), and the shape steps aside.
"""
from __future__ import annotations

import ctypes
import sys
import time
from typing import Callable, Optional

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import (QAbstractButton, QButtonGroup, QGraphicsOpacityEffect, QHBoxLayout,
                             QStackedWidget, QVBoxLayout, QWidget)

from ui import app_theme as T
from ui import kit, tokens
from ui.app_shell import IconButton
from ui.app_widgets import button
from ui.flow_bar import place_card

OPEN_W, OPEN_H = 520, 420           # DESIGN.md §2
PAD = 24                            # room around the shape in its window, for the shadow
LIFT = 6                            # the page rises this far as it fades in
TOP_BAR_H = 52
# A click outside closes the shape - and that click may be on the pill,
# which would open it again at once. A toggle this soon after is that click.
REOPEN_GUARD_S = 0.35

FOOT_H = 52

# (key, icon, name) - the tabs, left to right. A key in PAGES is the shape's
# own page; any other opens the app window there (app_pages.PAGES keys).
TABS = (
    ("home", "home", "Home"),
    ("insights", "insights", "Insights"),
    ("agents", "agents", "Jobs"),
)


def _pages() -> dict:
    from ui.shape_pages import HomeTab
    return {"home": HomeTab}


def animations_on() -> bool:
    """Windows' "Animation effects" (Settings > Accessibility > Visual
    effects). Off, the shape appears and goes at once."""
    if sys.platform != "win32":
        return True
    try:
        on = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0):
            return bool(on.value)
    except Exception:  # noqa: BLE001 - assume the default
        pass
    return True


def _part(t: float, start: float, length: float) -> float:
    """0-1 progress of a part of the animation that runs from `start` for `length`."""
    return max(0.0, min(1.0, (t - start) / length))


class _Tab(IconButton):
    """A tab in the top bar: the title bar's icon button, on a plate while chosen."""

    def __init__(self, name: str, tip: str):
        super().__init__(name, tip)
        self.setCheckable(True)

    def paintEvent(self, event) -> None:
        if self.isChecked():
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.NAV_PLATE))
            p.drawRoundedRect(QRectF(self.rect()), tokens.RADIUS_SM, tokens.RADIUS_SM)
            p.end()
        super().paintEvent(event)


class Shape(QWidget):
    """open_from(edge, pill) / shrink() / toggle(edge, pill). `bridge` is the
    app's AppBridge; `open_app(key)` opens the app window at a page key
    ("insights", "agents/job/<id>", "settings/plans", "invite") for what the
    shape has no page of its own for yet."""

    def __init__(self, bridge, open_app: Optional[Callable[[str], None]] = None):
        super().__init__()
        self.bridge = bridge
        self.open_app = open_app or (lambda _key: None)
        self.setWindowTitle("HeyTora")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(OPEN_W + 2 * PAD, OPEN_H + 2 * PAD)

        self._t = 0.0                       # 0 = the pill, 1 = open
        self._from = QRectF()               # the pill, in this window's coordinates
        self._opening = False
        self._closed_at = 0.0
        self._anim: Optional[QVariantAnimation] = None
        self._pages: dict[str, QWidget] = {}
        self._pending: set[str] = set()
        self._refresh = QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(150)      # one redraw per burst, as the app window
        self._refresh.timeout.connect(self._refresh_now)
        bridge.changed.connect(self._on_changed)

        self.body = QWidget(self)
        self.body.setObjectName("shapeBody")
        self.body.setStyleSheet(T.qss())
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_top_bar())
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        lay.addWidget(self._build_foot())
        self._fade = QGraphicsOpacityEffect(self.body)
        self._fade.setOpacity(0.0)
        self.body.setGraphicsEffect(self._fade)
        self.body.hide()
        self.show_tab(TABS[0][0])

    # ── the top bar and the foot ─────────────────────────────────────────────
    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(TOP_BAR_H)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(4)
        self._tabs: dict[str, _Tab] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, icon_name, name in TABS:
            tab = _Tab(icon_name, name)
            tab.clicked.connect(lambda _c=False, k=key: self.show_tab(k))
            group.addButton(tab)
            lay.addWidget(tab)
            self._tabs[key] = tab
        lay.addStretch(1)
        self.upgrade = button("Upgrade", "primary")
        self.upgrade.clicked.connect(lambda: self._elsewhere("settings/plans"))
        lay.addWidget(self.upgrade)
        gear = IconButton("settings", "Settings")
        gear.clicked.connect(lambda: self._elsewhere("settings/general"))
        lay.addWidget(gear)
        return bar

    def _build_foot(self) -> QWidget:
        foot = QWidget()
        foot.setFixedHeight(FOOT_H)
        lay = QHBoxLayout(foot)
        lay.setContentsMargins(16, 0, 16, 12)
        invite = button("Invite && earn", "ghost")        # && - a lone & would underline the e
        invite.clicked.connect(lambda: self._elsewhere("invite"))
        lay.addWidget(invite)
        lay.addStretch(1)
        jobs = button("Jobs +", "ghost")
        jobs.clicked.connect(lambda: self._elsewhere("agents"))
        lay.addWidget(jobs)
        return foot

    def _elsewhere(self, key: str) -> None:
        """What the shape has no page for yet: the app window, there."""
        self.shrink()
        self.open_app(key)

    # ── pages ────────────────────────────────────────────────────────────────
    def show_tab(self, key: str) -> None:
        kinds = _pages()
        if key not in kinds:
            # Not built for the shape yet: the tab stays where it was.
            if self._current():
                self._tabs[self._current()].setChecked(True)
            self._elsewhere(key)
            return
        page = self._pages.get(key)
        if page is None:
            page = kinds[key](self.bridge)
            page.navigate.connect(self._navigate)
            self._pages[key] = page
            self.stack.addWidget(page)
            self.body.setStyleSheet(T.qss())    # polish the new page's labels at the right size
        elif getattr(page, "LIVE", False):
            page.refresh()
        self.stack.setCurrentWidget(page)
        self._tabs[key].setChecked(True)

    def _current(self) -> str:
        page = self.stack.currentWidget()
        return next((k for k, w in self._pages.items() if w is page), "")

    def _navigate(self, key: str) -> None:
        if key in _pages():
            self.show_tab(key)
        else:
            self._elsewhere(key)

    def _on_changed(self, topic: str) -> None:
        self._pending.add(topic)
        self._refresh.start()

    def _refresh_now(self) -> None:
        """Redraw the page on show if it is live and shows what changed (Page.TOPICS)."""
        if not self.isVisible():
            return
        topics, self._pending = self._pending, set()
        page = self.stack.currentWidget()
        if page is not None and getattr(page, "LIVE", False) and set(getattr(page, "TOPICS", ())) & topics:
            partial = getattr(page, "refresh_topics", None)
            partial(topics) if partial is not None else page.refresh()

    def retheme(self) -> None:
        self.body.setStyleSheet(T.qss())
        page = self.stack.currentWidget()
        if page is not None:
            page.refresh()
        for w in self.findChildren(QWidget):
            w.update()
        self.update()

    # ── opening and closing ──────────────────────────────────────────────────
    def is_open(self) -> bool:
        return self.isVisible() and self._opening

    def toggle(self, edge: str, pill: QRectF) -> None:
        if self.is_open():
            self.shrink()
        elif time.monotonic() - self._closed_at > REOPEN_GUARD_S:
            self.open_from(edge, pill)

    def open_from(self, edge: str, pill: QRectF) -> None:
        """Grow out of `pill` (global coordinates), docked on `edge`."""
        screen = QGuiApplication.screenAt(pill.center().toPoint()) or QGuiApplication.primaryScreen()
        x, y = place_card(edge, pill, screen.availableGeometry(), OPEN_W, OPEN_H)
        self.move(int(round(x)) - PAD, int(round(y)) - PAD)
        self._from = pill.translated(-self.x(), -self.y())
        self.body.setGeometry(self._full().toRect())
        if self._fade.opacity() == 0.0:      # first time, or after a close
            self._t = 0.0
        self._opening = True
        self.show()
        self.raise_()
        self.activateWindow()
        page = self.stack.currentWidget()
        if page is not None and getattr(page, "LIVE", False):
            page.refresh()
        self._run(1.0, tokens.SHAPE_OPEN)

    def shrink(self) -> None:
        """Back into the pill."""
        if not self._opening:
            return
        self._opening = False
        self._closed_at = time.monotonic()
        self._run(0.0, tokens.SHAPE_CLOSE)

    def _run(self, to: float, ms: int) -> None:
        if self._anim is not None:
            self._anim.stop()
        if not animations_on():
            self._step(to)
            return
        a = QVariantAnimation(self)
        a.setStartValue(self._t)
        a.setEndValue(to)
        a.setDuration(max(1, int(ms * abs(to - self._t))))
        a.valueChanged.connect(lambda v: self._step(float(v)))
        a.start()
        self._anim = a

    def _step(self, t: float) -> None:
        self._t = t
        fade = tokens.ease().valueForProgress(_part(t, 0.4, 0.6))
        self._fade.setOpacity(fade)
        # Only while fading: an effect draws the page through a pixmap, which
        # can soften text on a scaled screen.
        self._fade.setEnabled(fade < 1.0)
        self.body.setVisible(fade > 0.0)
        full = self._full().toRect()
        self.body.move(full.x(), full.y() + round(LIFT * (1.0 - fade)))
        self.update()
        if t <= 0.0 and not self._opening:
            self.hide()

    def _full(self) -> QRectF:
        return QRectF(PAD, PAD, OPEN_W, OPEN_H)

    def shape_rect(self) -> QRectF:
        """Where the shape is painted now: the pill, the full size, or between."""
        g = tokens.ease().valueForProgress(_part(self._t, 0.0, 0.6))
        a, b = self._from if not self._from.isNull() else self._full(), self._full()
        return QRectF(a.x() + (b.x() - a.x()) * g, a.y() + (b.y() - a.y()) * g,
                      a.width() + (b.width() - a.width()) * g, a.height() + (b.height() - a.height()) * g)

    # ── painting and keys ────────────────────────────────────────────────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.shape_rect()
        g = tokens.ease().valueForProgress(_part(self._t, 0.0, 0.6))
        radius = min(r.height(), r.width()) / 2 * (1 - g) + tokens.RADIUS_LG * g
        kit.soft_shadow(p, r, radius, "float", strength=g)
        p.setBrush(kit.mix(tokens.PILL, T.BG, g))
        p.setPen(QPen(QColor(T.BORDER), 1.0) if T.is_dark() else Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, radius, radius)
        p.end()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.shrink()
            return
        super().keyPressEvent(e)

    def changeEvent(self, e) -> None:
        # A click somewhere else: the shape goes, as a menu does.
        if e.type() == e.Type.ActivationChange and not self.isActiveWindow() and self._opening:
            QTimer.singleShot(0, lambda: self.shrink() if not self.isActiveWindow() else None)
        super().changeEvent(e)
