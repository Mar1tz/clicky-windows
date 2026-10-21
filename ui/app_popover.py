"""The small cards that drop out of the shell: profile, notifications, the
free month, and help.

They open over the app window, not over the desktop, so they are solid
cards with a float shadow - glass is only for what floats over other apps
(DESIGN.md §2). Each is built fresh when it opens, so it shows what is true
now and wears the current theme.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QPoint, QPropertyAnimation, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui import app_theme as T
from ui import kit, tokens
from ui.app_widgets import icon

SHADOW = 28          # room around the card for its shadow
SUPPORT_EMAIL = "support@agentora.co.za"     # heytora-web src/lib/site.ts


class Popover(QWidget):
    """A card that closes on a click anywhere else, or Escape."""

    def __init__(self, width: int = 340):
        super().__init__(None, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._fade: Optional[QPropertyAnimation] = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW // 2, SHADOW, SHADOW)
        self.card = QFrame()
        self.card.setObjectName("popCard")
        self.card.setFixedWidth(width)
        kit.drop_shadow(self.card, "float")
        outer.addWidget(self.card)
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(6, 6, 6, 6)
        self.body.setSpacing(0)
        self.setStyleSheet(f"""
            QWidget {{ font-family: "{T.font()}", "{tokens.FALLBACK_FONT}"; color: {T.TEXT}; }}
            QFrame#popCard {{
                background: {T.SURFACE}; border: 1px solid {T.BORDER};
                border-radius: {tokens.RADIUS_LG}px;
            }}
            QFrame#popSep {{ background: {T.BORDER}; border: none; }}
            QFrame#popRow {{ background: transparent; border: none; border-radius: {tokens.RADIUS_SM}px; }}
            QFrame#popRow:hover {{ background: {T.SURFACE_HOVER}; }}
            QLabel {{ background: transparent; }}
            QLabel#popTitle {{ font-size: 15px; font-weight: 700; }}
            QLabel#popName  {{ font-size: 15px; font-weight: 700; }}
            QLabel#popText  {{ font-size: 13px; font-weight: 600; }}
            QLabel#popSub   {{ color: {T.TEXT_DIM}; font-size: 12px; }}
            QLabel#popSoon  {{
                background: {T.SURFACE_HOVER}; color: {T.TEXT_DIM}; border-radius: 11px;
                font-size: 12px; font-weight: 600; padding: 3px 10px;
            }}
        """)

    # ── pieces ───────────────────────────────────────────────────────────────

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def separator(self) -> None:
        line = QFrame()
        line.setObjectName("popSep")
        line.setFixedHeight(1)
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.addWidget(line)
        self.body.addWidget(wrap)

    def row(self, text: str, *, icon_name: str = "", sub: str = "",
            trailing: Optional[QWidget] = None, action: Optional[Callable] = None) -> QFrame:
        """A line in the card. With an action it lights under the pointer
        and runs it after the card has closed."""
        r = _Row(self, action)
        lay = QHBoxLayout(r)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        if icon_name:
            glyph = QLabel()
            glyph.setPixmap(icon(icon_name, T.TEXT_DIM, 16))
            lay.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.addWidget(_label(text, "popText"))
        if sub:
            text_col.addWidget(_label(sub, "popSub", wrap=True))
        lay.addLayout(text_col, 1)
        if trailing is not None:
            lay.addWidget(trailing, 0, Qt.AlignmentFlag.AlignVCenter)
        self.body.addWidget(r)
        return r

    # ── showing ──────────────────────────────────────────────────────────────

    def open_at(self, point: QPoint, align: str = "right") -> None:
        """Open with the card's top corner at `point` (global): its right
        corner for align="right", its left for "left"."""
        self.adjustSize()
        x = point.x() - self.width() + SHADOW if align == "right" else point.x() - SHADOW
        self.move(x, point.y() - SHADOW // 2)
        self.setWindowOpacity(0.0)
        self.show()
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(tokens.DURATION_FAST)
        fade.setEasingCurve(tokens.ease())
        fade.start()
        self._fade = fade

    def close_then(self, action: Optional[Callable]) -> None:
        self.close()
        if action is not None:
            QTimer.singleShot(0, action)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


class _Row(QFrame):
    def __init__(self, popover: Popover, action: Optional[Callable]):
        super().__init__()
        self.setObjectName("popRow" if action else "popStatic")
        self._popover, self._action = popover, action
        if action:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if self._action and event.button() == Qt.MouseButton.LeftButton \
                and self.rect().contains(event.position().toPoint()):
            self._popover.close_then(self._action)
            return
        super().mouseReleaseEvent(event)


def _label(text: str, name: str, *, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def coming_soon() -> QLabel:
    return _label("Coming soon", "popSoon")


class Avatar(QWidget):
    """The person's initial on the brand ribbon, in a circle."""

    def __init__(self, initial: str, size: int = 40):
        super().__init__()
        self._initial = (initial or "?")[:1].upper()
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        for i, colour in enumerate(tokens.RIBBON):
            g.setColorAt(i / (len(tokens.RIBBON) - 1), QColor(colour))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(r)
        p.setPen(QColor(tokens.ON_COLOUR))
        p.setFont(kit.font(r.height() * 0.42, 700))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._initial)
        p.end()


# ── the four cards ────────────────────────────────────────────────────────────

def _call(bridge, name: str, default=None):
    fn = getattr(bridge, name, None)
    try:
        return fn() if fn is not None else default
    except Exception:  # noqa: BLE001 - a card shows what it can
        return default


def profile_card(bridge, on_sign_out: Callable) -> Popover:
    """Who is signed in, their plan, the free month, account, sign out."""
    pop = Popover(340)
    st = _call(bridge, "account_state", {}) or {}
    email = st.get("email") or ""
    name = _call(bridge, "user_name", "") or (email.split("@")[0] if email else "You")
    head = QWidget()
    lay = QHBoxLayout(head)
    lay.setContentsMargins(12, 12, 12, 12)
    lay.setSpacing(14)
    lay.addWidget(Avatar(name, 44), 0, Qt.AlignmentFlag.AlignVCenter)
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(_label(name, "popName"))
    if email:
        col.addWidget(_label(email, "popSub"))
    summary = st.get("summary") or {}
    if summary:
        plan = summary.get("plan_name") or (summary.get("plan") or "").capitalize() or "HeyTora"
        line = f"{plan} plan"
        from account.service import credits_left
        left = credits_left(summary)
        if left is not None:
            line += f" · {left:,} credits left"
        col.addWidget(_label(line, "popSub"))
    lay.addLayout(col, 1)
    pop.add(head)
    pop.separator()
    pop.row("Get a free month of Pro", sub="Refer friends, earn rewards",
            trailing=coming_soon())
    pop.separator()
    if st.get("website"):
        pop.row("Manage account", icon_name="account", action=bridge.open_website)
    if st.get("signed_in"):
        pop.row("Sign out", icon_name="sign_out", action=on_sign_out)
    return pop


def notifications_card(bridge) -> Popover:
    """TODO(notifications): the feed - agent tasks finished or waiting for
    approval, how far along each one is, this month's usage, and
    achievements ("your first walkthrough", "100 questions"). Until then the
    card says what will be here."""
    pop = Popover(380)
    head = QWidget()
    lay = QHBoxLayout(head)
    lay.setContentsMargins(14, 12, 14, 4)
    lay.addWidget(_label("Notifications", "popTitle"))
    pop.add(head)
    empty = QWidget()
    col = QVBoxLayout(empty)
    col.setContentsMargins(14, 18, 14, 22)
    col.setSpacing(6)
    badge = _Badge("achievement")
    col.addWidget(badge, 0, Qt.AlignmentFlag.AlignHCenter)
    col.addSpacing(6)
    title = _label("Nothing yet", "popText")
    title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    col.addWidget(title)
    sub = _label("Agent progress, your usage and achievements will show up here.",
                 "popSub", wrap=True)
    sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    col.addWidget(sub)
    pop.add(empty)
    return pop


def free_month_card() -> Popover:
    pop = Popover(360)
    pop.row("Get a free month",
            sub="Invite a friend to HeyTora and you both get a month of Pro.",
            trailing=coming_soon())
    return pop


def help_card(bridge, show_page: Callable[[str], None]) -> Popover:
    pop = Popover(280)

    def mail() -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(f"mailto:{SUPPORT_EMAIL}"))

    pop.row("Contact support", icon_name="support", action=mail)
    if hasattr(bridge, "save_report"):
        pop.row("Save a problem report", icon_name="report", action=bridge.save_report)
    pop.row("Keys and diagnostics", icon_name="key-round", action=lambda: show_page("setup"))
    return pop


class _Badge(QWidget):
    """An icon on a pale accent tile - the notification's picture."""

    def __init__(self, name: str, size: int = 52):
        super().__init__()
        self._name = name
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(tokens.BLUE_TINT_DARK if T.is_dark() else tokens.BLUE_TINT))
        p.drawRoundedRect(QRectF(self.rect()), tokens.RADIUS_MD, tokens.RADIUS_MD)
        s = 26
        p.drawPixmap((self.width() - s) // 2, (self.height() - s) // 2, icon(self._name, T.ACCENT, s))
        p.end()
