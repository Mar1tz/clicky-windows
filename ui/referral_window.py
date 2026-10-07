"""Get a free month: the referral window (docs/REFERRALS.md).

Wispr Flow's layout - Refer, Past referrals, Apply referral - for HeyTora's
offer: a friend gets 14 days of Pro, and when they've asked Tora 15
questions the referrer gets a free month, used on their next payment. Every
rule is the server's (migration 0010); this window shows what it says, and
in words why a code can't be used.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from ui import app_theme as T
from ui import tokens
from ui.app_widgets import button, icon, label
from ui.settings_parts import SubDialog

GOAL = 15                 # questions the friend asks (migration 0010)
PANEL_H = 440             # each tab's panel: as tall as Refer's
TABS = (("refer", "Refer"), ("past", "Past referrals"), ("apply", "Apply referral"))


class GiftCard(QWidget):
    """The gift, after Wispr's: HeyTora Pro, 14 days free, gifted by you."""

    def __init__(self, name: str):
        super().__init__()
        self._name = name
        self.setFixedHeight(170)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(40, 4, -40, -4)
        a, b = tokens.WORK_TILES["ledger"]
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, QColor(a))
        g.setColorAt(1, QColor(b))
        clip = QPainterPath()
        clip.addRoundedRect(r, 18, 18)
        p.setClipPath(clip)
        p.fillRect(r, g)
        # the swoosh
        wave = QPainterPath()
        wave.moveTo(r.left() - 10, r.top() + 34)
        wave.cubicTo(r.left() + r.width() * 0.35, r.top() + 4, r.left() + r.width() * 0.6, r.top() + 70,
                     r.right() + 10, r.top() + 10)
        white = QColor(tokens.ON_COLOUR)
        white.setAlpha(150)
        p.setPen(QPen(white, 5))
        p.drawPath(wave)
        p.setClipping(False)
        p.setPen(QColor(tokens.ON_COLOUR))
        f = QFont(self.font())
        f.setPixelSize(30)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(QRectF(r.left(), r.top() + 46, r.width(), 40), Qt.AlignmentFlag.AlignCenter, "HeyTora  Pro")
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        p.setFont(f)
        p.drawText(QRectF(r.left(), r.top() + 90, r.width(), 20), Qt.AlignmentFlag.AlignCenter,
                   "14 DAYS OF PRO, FREE")
        if self._name:
            words = f"Gifted by {self._name}"
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0)
            f.setPixelSize(11)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(words) + 22
            pill = QRectF(r.center().x() - w / 2, r.top() + 122, w, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(tokens.ON_COLOUR))
            p.drawRoundedRect(pill, 11, 11)
            p.setPen(QColor(b))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, words)
        p.end()


def _how_row(icon_name: str, words: str) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    pic = QLabel()
    pic.setPixmap(icon(icon_name, T.TEXT_DIM, 16))
    lay.addWidget(pic, 0, Qt.AlignmentFlag.AlignTop)
    lay.addWidget(label(words, "setDesc", wrap=True), 1)
    return w


def _friend_line(f: dict) -> str:
    if f.get("status") == "withdrawn":
        return "Withdrawn"
    if f.get("earned"):
        return "Earned you a free month"
    if f.get("status") == "qualified":
        return "Counted - you've reached this year's 12 free months"
    return f"Joined · {min(GOAL, int(f.get('asked') or 0))} of {GOAL} questions"


class ReferralWindow(SubDialog):
    """The three tabs. Reads the server off the UI thread on opening."""

    W = 580

    def __init__(self, over: QWidget, bridge, tab: str = "refer"):
        super().__init__(over, "Refer and earn rewards",
                         "Give a friend 14 days of Pro, and get a free month when they've asked "
                         f"Tora {GOAL} questions.")
        self.bridge = bridge
        self._tab = tab
        self._state: Optional[dict] = None
        self._busy = None
        self.tabs_row = QHBoxLayout()
        self.body.addLayout(self.tabs_row)
        self.content = QVBoxLayout()
        self.content.setSpacing(12)
        self.body.addLayout(self.content)
        self.foot_note = label("", "setNote", wrap=True)
        self.foot_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.foot.addWidget(self.foot_note, 1)
        self._draw_tabs()
        self._show_loading()
        self._load()

    # -- loading -------------------------------------------------------------------------

    def _load(self) -> None:
        from ui.settings_tabs import _Background
        self._busy = _Background(self, self.bridge.referral_status, self._loaded)

    def _loaded(self, state: dict) -> None:
        self._busy = None
        self._state = state
        self._draw_tabs()
        self._show(self._tab)

    def _show_loading(self) -> None:
        self._clear()
        self.content.addWidget(label("Getting your invite link…", "setDesc"))

    # -- the tabs ------------------------------------------------------------------------

    def _draw_tabs(self) -> None:
        from ui.app_parts import TabBar
        while self.tabs_row.count():
            w = self.tabs_row.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        n = len((self._state or {}).get("friends") or [])
        names = [(k, f"{w} ({n})" if k == "past" else w) for k, w in TABS]
        bar = TabBar(names, self._tab)
        bar.picked.connect(self._show)
        self.tabs_row.addWidget(bar)

    def _clear(self) -> None:
        while self.content.count():
            item = self.content.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _show(self, tab: str) -> None:
        self._tab = tab
        self._clear()
        st = self._state or {}
        if st.get("error"):
            self.content.addWidget(label(st["error"], "setDesc", wrap=True))
            again = button("Try again")
            again.clicked.connect(lambda: (self._show_loading(), self._load()))
            self.content.addWidget(again, 0, Qt.AlignmentFlag.AlignLeft)
            self.foot_note.setText("")
        else:
            {"refer": self._refer, "past": self._past, "apply": self._apply}[tab](st)
        self._place()

    def _panel(self) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setObjectName("setBlock")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(22, 18, 22, 20)
        lay.setSpacing(10)
        panel.setMinimumHeight(PANEL_H)          # the same on every tab: the window doesn't jump
        self.content.addWidget(panel)
        return panel, lay

    def _refer(self, st: dict) -> None:
        _panel, lay = self._panel()
        from config import cfg
        lay.addWidget(GiftCard(cfg.profile()[0].split(" ")[0] if cfg.profile()[0] else ""))
        lay.addSpacing(4)
        lay.addWidget(label("How it works", "setTitle"))
        lay.addWidget(_how_row("megaphone", "Share your invite link"))
        lay.addWidget(_how_row("crown", "They sign up and get 14 days of Pro, free"))
        lay.addWidget(_how_row("party-popper",
                               f"You get a free month when they've asked Tora {GOAL} questions"))
        lay.addSpacing(6)
        head = QHBoxLayout()
        head.addWidget(label("Your invite link", "setTitle"))
        refresh = QLabel()
        refresh.setPixmap(icon("refresh-cw", T.TEXT_FAINT, 14))
        refresh.setToolTip("New link - the old one stops working for new sign-ups")
        refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh.mousePressEvent = lambda _e: self._refresh_code()
        head.addWidget(refresh)
        head.addStretch(1)
        lay.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.link_edit = QLineEdit(st.get("link") or st.get("code") or "")
        self.link_edit.setReadOnly(True)
        row.addWidget(self.link_edit, 1)
        self.copy_btn = button("Copy")
        self.copy_btn.clicked.connect(self._copy)
        row.addWidget(self.copy_btn)
        lay.addLayout(row)
        lay.addWidget(label(f"Your code: {st.get('code', '')}", "setNote"))
        waiting = int(st.get("months_waiting") or 0)
        if waiting:
            lay.addWidget(label(f"You have {waiting} free month{'s' if waiting != 1 else ''} waiting.",
                                "setTitle"))
        self.foot_note.setText("Free months apply to your next payment. "
                               "Subscribe in the app to use them.")

    def _past(self, st: dict) -> None:
        friends = st.get("friends") or []
        _panel, lay = self._panel()
        if not friends:
            lay.addStretch(1)
            title = label("You haven't referred anyone yet", "setTitle")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(title)
            sub = label(f"Invite a friend, and get a free month when they've asked Tora {GOAL} "
                        "questions.", "setDesc", wrap=True)
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(sub)
            lay.addStretch(1)
        else:
            for f in friends:
                row = QHBoxLayout()
                row.addWidget(label(f.get("name") or "A friend", "setTitle"), 1)
                row.addWidget(label(_friend_line(f), "setDesc"))
                lay.addLayout(row)
            lay.addStretch(1)
        waiting, used = int(st.get("months_waiting") or 0), int(st.get("months_used") or 0)
        self.foot_note.setText(f"Free months: {waiting} waiting · {used} used" if waiting or used else "")

    def _apply(self, st: dict) -> None:
        _panel, lay = self._panel()
        lay.addWidget(label("Redeem a referral code", "setTitle"))
        if st.get("referred_by"):
            lay.addWidget(label(f"You joined through {st['referred_by']}'s invite.", "setDesc", wrap=True))
        if st.get("trial_until"):
            from ui.settings_tabs import _day
            lay.addWidget(label(f"Your Pro trial runs until {_day(st['trial_until'])}.", "setDesc"))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("e.g. DYLANH4821")
        self.code_edit.setMaxLength(20)
        self.apply_btn = button("Apply", "primary")
        self.apply_btn.clicked.connect(self._apply_code)
        self.code_edit.returnPressed.connect(self._apply_code)
        row.addWidget(self.code_edit, 1)
        row.addWidget(self.apply_btn)
        lay.addLayout(row)
        self.apply_note = label("", "setDesc", wrap=True)
        lay.addWidget(self.apply_note)
        if not st.get("new_account"):
            self.code_edit.setEnabled(False)
            self.apply_btn.setEnabled(False)
            self.apply_note.setText("Codes are for new accounts, in their first 7 days."
                                    if not st.get("referred_by") else "")
        lay.addStretch(1)
        self.foot_note.setText("Your 14 days of Pro start as soon as a code is applied.")

    # -- doing -----------------------------------------------------------------------------

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.link_edit.text())
        self.copy_btn.setText("Copied")
        QTimer.singleShot(1500, lambda: self.copy_btn.setText("Copy") if not self.isHidden() else None)

    def _refresh_code(self) -> None:
        if self._busy is not None:
            return
        from ui.settings_tabs import _Background

        def done(answer: dict) -> None:
            self._busy = None
            if answer.get("error"):
                self.foot_note.setText(answer["error"])
                return
            self._show_loading()
            self._load()
        self._busy = _Background(self, self.bridge.refresh_referral_code, done)

    def _apply_code(self) -> None:
        code = self.code_edit.text().strip()
        if not code or self._busy is not None:
            return
        from ui.settings_tabs import _Background
        self.apply_btn.setEnabled(False)
        self.apply_note.setText("Checking…")

        def done(answer: dict) -> None:
            self._busy = None
            self.apply_btn.setEnabled(True)
            if answer.get("ok"):
                who = answer.get("referrer_name") or "Your friend"
                self.apply_note.setText(f"Done - {who} gave you 14 days of Pro. Ask Tora {GOAL} questions "
                                        "and they get a free month too.")
                self.code_edit.setEnabled(False)
                self.apply_btn.setEnabled(False)
            else:
                self.apply_note.setText(answer.get("message") or answer.get("error")
                                        or "That code can't be used.")
        self._busy = _Background(self, lambda: self.bridge.apply_referral(code), done)


def open_referrals(window: QWidget, bridge, tab: str = "refer") -> ReferralWindow:
    """Over the app window (or Settings, when it is open)."""
    over = getattr(window, "_settings", None)
    over = over if over is not None and over.isVisible() else window
    dlg = ReferralWindow(over, bridge, tab)
    dlg.open()
    return dlg
