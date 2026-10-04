"""The pieces every page of the app window is built from.

Nine pages that each invent their own card, their own row and their own
toggle is how a product ends up looking like nine products. These are the
shared parts: a card, a section heading, a metric tile, a list row, a status
pill, a switch, a progress bar. Colour and spacing come from app_theme, so a
change there moves the whole app at once.

None of these are wired to anything yet — a Switch remembers its own state
and a Meter draws the number it was given. Behaviour comes later; this is
the shape of the thing.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from ui import app_theme as T
from ui import tokens


# ── text ──────────────────────────────────────────────────────────────────────

def label(text: str, name: str = "", *, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def title(text: str) -> QLabel:
    return label(text, "pageTitle")


def lede(text: str) -> QLabel:
    return label(text, "pageLede", wrap=True)


def section(text: str) -> QLabel:
    return label(text.upper(), "sectionTitle")


def hrule() -> QFrame:
    line = QFrame()
    line.setObjectName("hr")
    line.setFixedHeight(1)
    return line


def spacer(h: int = 0) -> QWidget:
    w = QWidget()
    if h:
        w.setFixedHeight(h)
    else:
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return w


# ── buttons ───────────────────────────────────────────────────────────────────

def button(text: str, kind: str = "ghost") -> QPushButton:
    """kind: primary | ghost | danger | link."""
    b = QPushButton(text)
    b.setObjectName(kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


# ── dot ───────────────────────────────────────────────────────────────────────

class Dot(QWidget):
    """A small filled circle, optionally with a soft halo. Used for agent
    status, the state pill and the wordmark."""

    def __init__(self, colour: Optional[str] = None, size: int = 8, glow: bool = False):
        super().__init__()
        colour = colour or T.OK
        self._colour = QColor(colour)
        self._glow = glow
        # The halo is twice the dot across. The widget used to be only 6 px
        # bigger than the dot, so the halo was clipped into a square.
        side = size * 2 if glow else size
        self.setFixedSize(side, side)
        self._size = size

    def set_colour(self, colour: str) -> None:
        self._colour = QColor(colour)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        cx, cy = r.center().x() + 0.5, r.center().y() + 0.5
        if self._glow:
            halo = QColor(self._colour)
            halo.setAlpha(60)
            p.setBrush(halo)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx - self._size, cy - self._size,
                                 self._size * 2, self._size * 2))
        p.setBrush(self._colour)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - self._size / 2, cy - self._size / 2,
                             self._size, self._size))
        p.end()


# ── pill ──────────────────────────────────────────────────────────────────────

class Pill(QFrame):
    """A rounded tag: "Running", "3 waiting", "Read only"."""

    def __init__(self, text: str, colour: Optional[str] = None, *, dot: bool = False):
        super().__init__()
        colour = colour or T.TEXT_DIM
        self._colour = colour
        self.setFixedHeight(22)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 0, 10, 0)
        lay.setSpacing(6)
        if dot:
            lay.addWidget(Dot(colour, 6))
        self._label = QLabel(text)
        self._label.setStyleSheet(
            f"color: {colour}; font-size: 11px; font-weight: 600;"
            " background: transparent; border: none;")
        lay.addWidget(self._label)
        # Scoped to this frame by name: a bare "QFrame" selector also matched
        # the QLabel inside (a QLabel IS a QFrame), which drew a second,
        # square border inside every pill.
        self.setObjectName("pill")
        self.setStyleSheet(
            f"QFrame#pill {{ background: {_alpha(colour, 0.10)};"
            f" border: 1px solid {_alpha(colour, 0.30)};"
            f" border-radius: 11px; }}")

    def set_text(self, text: str) -> None:
        self._label.setText(text)


def _alpha(hex_or_rgba: str, a: float) -> str:
    """rgba() string for a #rrggbb colour. Pass-through for anything else."""
    c = QColor(hex_or_rgba)
    if not c.isValid():
        return hex_or_rgba
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a})"


# ── card ──────────────────────────────────────────────────────────────────────

class Card(QFrame):
    """A titled surface. `body` is the layout callers add their rows to."""

    def __init__(self, heading: str = "", sub: str = "", *,
                 quiet: bool = False, action: Optional[QPushButton] = None):
        super().__init__()
        self.setObjectName("cardQuiet" if quiet else "card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        if heading:
            head = QHBoxLayout()
            head.setSpacing(10)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(label(heading, "cardTitle"))
            if sub:
                text.addWidget(label(sub, "cardSub", wrap=True))
            head.addLayout(text, 1)
            if action is not None:
                head.addWidget(action, 0, Qt.AlignmentFlag.AlignTop)
            outer.addLayout(head)

        self.body = QVBoxLayout()
        self.body.setSpacing(0)
        self.body.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.body)
        # Spare height collects here. Without it, a card that is shorter than
        # the one beside it grows by pushing its own subtitle away from its
        # title, which reads as a mistake rather than as breathing room.
        outer.addStretch(1)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget


# ── metric tile ───────────────────────────────────────────────────────────────

class Metric(QFrame):
    """A number worth looking at: 3 running · R 48,210 · 12 exceptions."""

    def __init__(self, value: str, caption: str, colour: Optional[str] = None):
        super().__init__()
        colour = colour or T.TEXT
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(2)
        v = label(value, "metricValue")
        v.setStyleSheet(f"color: {colour};")
        lay.addWidget(v)
        lay.addWidget(label(caption.upper(), "metricLabel"))


# ── list row ──────────────────────────────────────────────────────────────────

class Row(QFrame):
    """One line in a list: a leading dot, a title, a subtitle, a trailing
    widget. Every list in the app is this, so they all line up."""

    def __init__(self, heading: str, sub: str = "", *,
                 dot: Optional[str] = None, trailing: Optional[QWidget] = None,
                 first: bool = False, wrap: bool = False):
        super().__init__()
        self.setObjectName("cardQuiet" if first else "row")
        if first:
            self.setStyleSheet("QFrame { border: none; background: transparent; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 11, 0, 11)
        lay.setSpacing(12)
        if dot is not None:
            lay.addWidget(Dot(dot, 8), 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(2)
        # wrap: a heading nobody wrote to fit - a question someone asked, a
        # task's title. Unwrapped, one long line widens the whole page past
        # the window, and the page does not scroll sideways.
        text.addWidget(label(heading, wrap=wrap))
        if sub:
            text.addWidget(label(sub, "faint", wrap=True))
        lay.addLayout(text, 1)
        if trailing is not None:
            lay.addWidget(trailing, 0, Qt.AlignmentFlag.AlignVCenter)


# ── switch ────────────────────────────────────────────────────────────────────

class Switch(QWidget):
    """An iOS-style toggle. Draws itself, so it looks the same on every
    Windows theme — a QCheckBox does not."""

    toggled = pyqtSignal(bool)

    def __init__(self, on: bool = False):
        super().__init__()
        self._on = on
        self._pos = 1.0 if on else 0.0        # 0 = off, 1 = on; animated
        self._anim = None
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._on

    def setChecked(self, on: bool) -> None:
        if on != self._on:
            self._on = on
            self._slide(1.0 if on else 0.0)
            self.toggled.emit(on)

    def _slide(self, to: float) -> None:
        """The thumb travels on the site's easing curve rather than jumping:
        a switch that snaps reads as a checkbox in disguise."""
        from PyQt6.QtCore import QVariantAnimation
        from ui import tokens
        if self._anim is not None:
            self._anim.stop()
        if not self.isVisible():
            self._pos = to
            self.update()
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self._pos))
        anim.setEndValue(float(to))
        anim.setDuration(tokens.DURATION_FAST)
        anim.setEasingCurve(tokens.ease())
        anim.valueChanged.connect(self._moved)
        anim.start()
        self._anim = anim

    def _moved(self, value) -> None:
        self._pos = float(value)
        self.update()

    def mousePressEvent(self, _event) -> None:
        self.setChecked(not self._on)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off, on = QColor(T.BORDER_STRONG), QColor(T.ACCENT)
        t = self._pos
        track = QColor(int(off.red() + (on.red() - off.red()) * t),
                       int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        p.setBrush(track)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        x = 2.0 + 18.0 * t
        # A white thumb in both states, as iOS draws it, with a hairline
        # shadow so it still has an edge on a light track.
        p.setBrush(QColor(0, 0, 0, 30))
        p.drawEllipse(QRectF(x, 2.6, 18, 18))
        p.setBrush(QColor(tokens.ON_COLOUR))
        p.drawEllipse(QRectF(x, 2, 18, 18))
        p.end()


class ToggleRow(QFrame):
    """A setting: what it is, what it does, and the switch."""

    def __init__(self, heading: str, sub: str = "", on: bool = False,
                 *, first: bool = False):
        super().__init__()
        self.switch = Switch(on)
        self.setObjectName("cardQuiet" if first else "row")
        if first:
            self.setStyleSheet("QFrame { border: none; background: transparent; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addWidget(label(heading))
        if sub:
            text.addWidget(label(sub, "faint", wrap=True))
        lay.addLayout(text, 1)
        lay.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)


# ── progress ──────────────────────────────────────────────────────────────────

class Meter(QWidget):
    """A thin progress bar. QProgressBar cannot be made to look like this
    without more stylesheet than the widget is worth."""

    def __init__(self, fraction: float = 0.0, colour: str = T.ACCENT,
                 height: int = 6):
        super().__init__()
        self._f = max(0.0, min(1.0, fraction))
        self._colour = colour
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_fraction(self, f: float) -> None:
        self._f = max(0.0, min(1.0, f))
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()
        r = h / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.BORDER))
        p.drawRoundedRect(QRectF(0, 0, self.width(), h), r, r)
        if self._f > 0:
            p.setBrush(QColor(self._colour))
            p.drawRoundedRect(QRectF(0, 0, max(h, self.width() * self._f), h), r, r)
        p.end()


# ── select ────────────────────────────────────────────────────────────────────

class Select(QComboBox):
    """A combo box with a chevron you can actually see.

    Qt draws no arrow once a stylesheet has touched the widget, and every CSS
    way of putting one back collapses to a dash inside the down-arrow
    subcontrol or needs an image file on disk. Ten lines of QPainter is
    smaller than either, and a picker with no chevron is indistinguishable
    from a read-only field.
    """

    def __init__(self, *items: str, width: int = 0):
        super().__init__()
        self.addItems(list(items))
        if width:
            self.setFixedWidth(width)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(T.TEXT_DIM))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        x = self.width() - 20.0
        y = self.height() / 2 - 1.5
        path = QPainterPath()
        path.moveTo(x, y)
        path.lineTo(x + 4.5, y + 4.5)
        path.lineTo(x + 9, y)
        p.drawPath(path)
        p.end()


# ── scrolling page body ───────────────────────────────────────────────────────

class PageBody(QScrollArea):
    """A scrollable, centred column with the page gutter already applied.

    The column is never wider than T.COLUMN_W and sits in the middle of a
    wide window, as Wispr Flow's pages do - a page stretched edge to edge
    on a big screen reads as a spreadsheet. `top` holds what stays when a
    live page rebuilds (its title); `column` is what clear() empties.
    """

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        centre = QHBoxLayout(inner)
        centre.setContentsMargins(T.GUTTER, 0, T.GUTTER, T.GUTTER)
        centre.setSpacing(0)
        holder = QWidget()
        holder.setMaximumWidth(T.COLUMN_W)
        centre.addWidget(holder, 1)
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(T.GAP)
        self.top = QVBoxLayout()
        self.top.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.top)
        self.column = QVBoxLayout()
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(T.GAP)
        outer.addLayout(self.column)
        outer.addStretch(1)
        self.setWidget(inner)

    def add(self, widget: QWidget) -> QWidget:
        self.column.addWidget(widget)
        return widget

    def add_layout(self, layout) -> None:
        self.column.addLayout(layout)

    def finish(self) -> None:
        """Kept for callers: the column already sits at the top."""

    def clear(self) -> None:
        """Empty the column, for a page that rebuilds from live data."""
        def drop(layout) -> None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
                elif item.layout() is not None:
                    drop(item.layout())
        drop(self.column)


def refill(layout, widgets) -> None:
    """Swap a layout's widgets for new ones: a card whose rows are redrawn
    from live data while the rest of its page - anything embedded that
    keeps its own state - stays exactly where it is."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
    for widget in widgets:
        layout.addWidget(widget)


def columns(*widgets: QWidget, spacing: int = T.GAP) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        lay.addWidget(w, 1)
    return lay


def shadow(widget: QWidget, level: str = "float") -> None:
    """A floating widget's shadow, from the elevation scale (ui/kit.py)."""
    from ui import kit
    kit.drop_shadow(widget, level)


# ── icons ─────────────────────────────────────────────────────────────────────
#
# One outline set, Lucide (assets/icons, ISC licence), at one stroke weight:
# a 2px stroke on a 24px grid is 1.5px at the 18px the shell draws them, the
# weight of the 14px text beside them. Their stroke is `currentColor`, so a
# colour is a string replace before the render - crisp at any scale, and the
# same icon can be ink in one place and the accent in another.
#
# The set before this was nine glyphs painted with QPainter, and before that
# Unicode characters that fell back to three different fonts.

from PyQt6.QtGui import QPixmap   # noqa: E402  (grouped with its users)

# Page and action -> icon file. A new page adds a line here.
ICONS = {
    "home": "house",
    "agents": "bot",
    "journal": "book-open",
    "extensions": "blocks",
    "accounting": "calculator",
    "tora": "sparkles",
    "settings": "settings",
    "setup": "key-round",
    "help": "circle-help",
    "free_month": "gift",
    "sidebar": "panel-left",
    "profile": "circle-user-round",
    "notifications": "bell",
    "achievement": "award",
    "support": "mail",
    "report": "file-text",
    "account": "external-link",
    "sign_out": "log-out",
}

_svg_cache: dict[str, str] = {}


def icon(name: str, colour: str, size: int = 18, dpr: float = 2.0) -> QPixmap:
    """A Lucide icon (`name` is its file, or a key of ICONS) in `colour`."""
    from PyQt6.QtCore import QByteArray
    from PyQt6.QtSvg import QSvgRenderer
    name = ICONS.get(name, name)
    if name not in _svg_cache:
        _svg_cache[name] = (tokens.assets_dir() / "icons" / f"{name}.svg").read_text(encoding="utf-8")
    svg = _svg_cache[name].replace("currentColor", QColor(colour).name())
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p, QRectF(0, 0, size, size))
    p.end()
    return px


def nav_icon(kind: str, colour: str, size: int = 18, dpr: float = 2.0) -> QPixmap:
    """A page's icon. `kind` keys are the page ids in app_pages."""
    return icon(kind, colour, size, dpr)


# ── wordmark ──────────────────────────────────────────────────────────────────

def wordmark_pixmap(height: int = 24, ink: str = "", swoosh: str = "",
                    dpr: float = 2.0) -> QPixmap:
    """The site's lockup - lowercase `heytora` with the swoosh through it -
    at `height` px. The letters take the page's ink, so it reads on either
    ground; the swoosh keeps the logo's blue."""
    from PyQt6.QtCore import QByteArray
    from PyQt6.QtSvg import QSvgRenderer
    from ui import tokens
    svg = (tokens.assets_dir() / "brand" / "wordmark.svg").read_text(encoding="utf-8")
    svg = svg.replace("{INK}", ink or T.TEXT).replace("{SWOOSH}", swoosh or tokens.LOGO_BLUE)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    vb = renderer.viewBoxF()
    width = round(height * vb.width() / vb.height())
    px = QPixmap(int(width * dpr), int(height * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, width, height))
    p.end()
    return px


def wordmark(height: int = 24) -> QLabel:
    lab = QLabel()
    lab.setObjectName("wordmarkImage")
    lab.setAccessibleName("heytora")
    try:
        lab.setPixmap(wordmark_pixmap(height))
    except Exception:  # noqa: BLE001 - no SVG module or file: the name in type
        lab.setText("heytora")
        lab.setObjectName("wordmark")
    return lab
