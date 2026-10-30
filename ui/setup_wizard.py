"""
The sign-in window, and the starter skills that suit what someone does.

No HeyTora account, no HeyTora: the sign-in gate (ui/sign_in_gate.py) shows
this window before anything else starts, and the app opens it again
whenever Tora has no way to reach Gemini. Everything else the old first
launch did - the questions, the mic and shortcut tests, her voice and
colour - is the tour now, which grows out of the pill once the app is up
(ui/onboarding_shape.py, docs/ui/HEYCLICKY_ONBOARDING.md). The old wizard's
pages went in revamp step 6b. Since 6c the window is drawn as the tour's
panel is - one rounded card in the app's colours, no Windows frame, the
tour's title and grey line, Finish at the foot - so signing in and the tour
that follows look like one thing (DESIGN.md §10).

Signing in happens on the website (an emailed link, Google or Microsoft),
which hands the sign-in back to this PC (account/loopback.py). Once signed
in, the user may think with their own Gemini key instead. A build without
HeyTora's service filled in (account/settings.py) has no accounts and
offers only the key.

Network work (signing in, checking a key) runs on worker threads and
reports back through signals: the window never freezes.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QPoint, QRectF, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from config import cfg
from ui import app_theme as T
from ui import kit, tokens
from ui.app_widgets import button, label

_log = logging.getLogger("tora.setup")


# Marker file: the first launch's tour has run (or been closed once the app was up).
def _flag_path() -> Path:
    from config import data_dir
    return data_dir() / "setup_complete.flag"


def setup_already_ran() -> bool:
    return _flag_path().exists()


def mark_setup_complete() -> None:
    try:
        _flag_path().write_text("ok")
    except Exception:
        pass


# What the user does. The tour's interview hears it (ui/onboarding_interview.py),
# and it goes into Tora's instructions (cfg.profile). The second of each pair
# is the tile it is drawn with.
WORK = (
    ("Accounting", "ledger"),
    ("Business owner", "briefcase"),
    ("Student", "cap"),
    ("Teacher", "cap"),
    ("Developer", "code"),
    ("Sales", "chart"),
    ("Design & video", "video"),
    ("Admin & office", "calendar"),
    ("Writing", "mic"),
    ("Something else", "wallet"),
)
USES = tuple(w for w, _k in WORK)

# The starter skills (skills/*.md) that suit what you do. The tour turns
# these on and the other starter skills off - fewer for Tora to choose from,
# and Tora > Skills turns any of them back on (docs/agents/SKILLS.md).
SKILLS_FOR = {
    "Accounting": ("send-an-invoice", "file-receipts", "chase-an-unpaid-invoice",
                   "month-end-checklist", "make-a-quote"),
    "Business owner": ("send-an-invoice", "make-a-quote", "book-a-meeting",
                       "draft-an-email-reply", "chase-an-unpaid-invoice", "inbox-summary"),
    "Student": ("meeting-notes", "file-documents", "voice-note-to-email", "fill-a-form"),
    "Teacher": ("draft-an-email-reply", "meeting-notes", "file-documents", "fill-a-form"),
    "Developer": ("tidy-downloads", "file-documents", "inbox-summary"),
    "Sales": ("draft-an-email-reply", "book-a-meeting", "make-a-quote", "inbox-summary",
              "chase-an-unpaid-invoice"),
    "Design & video": ("social-post", "send-an-invoice", "make-a-quote", "tidy-downloads"),
    "Admin & office": ("inbox-summary", "book-a-meeting", "file-documents", "fill-a-form",
                       "month-end-checklist", "convert-to-pdf"),
    "Writing": ("voice-note-to-email", "draft-an-email-reply", "social-post"),
}
# Everyone gets these; "Something else" (or no answer) keeps every one on.
SKILLS_FOR_EVERYONE = ("draft-an-email-reply", "tidy-downloads", "compare-prices")

# Each starter skill's card: the tile of the work it suits, and what you'd
# say - the jobs made ready on Home when she made none (ui/ready_jobs.py).
SKILL_CARDS = {
    "send-an-invoice": ("ledger", "Invoice Sarah for last week"),
    "chase-an-unpaid-invoice": ("wallet", "Chase Bloom about invoice 12"),
    "file-receipts": ("ledger", "File these receipts for me"),
    "month-end-checklist": ("calendar", "Do my month-end"),
    "make-a-quote": ("briefcase", "Quote Bloom for a new website"),
    "book-a-meeting": ("calendar", "Book a call with Sam on Friday"),
    "draft-an-email-reply": ("briefcase", "Reply to this email for me"),
    "inbox-summary": ("briefcase", "What came in today?"),
    "voice-note-to-email": ("mic", "Turn what I said into an email"),
    "social-post": ("video", "Post this on LinkedIn"),
    "meeting-notes": ("mic", "Write up today's meeting"),
    "file-documents": ("cap", "File these documents"),
    "tidy-downloads": ("code", "Tidy my Downloads folder"),
    "compare-prices": ("chart", "Find the best price for a kettle"),
    "fill-a-form": ("cap", "Fill in this form for me"),
    "convert-to-pdf": ("ledger", "Turn this document into a PDF"),
}


def skills_for(uses) -> list[str]:
    """The starter skills for what someone does, best first, each once.
    Empty for "Something else" or no answer: then every one stays on."""
    if not uses or "Something else" in uses:
        return []
    out: list[str] = []
    for use in uses:
        for sid in SKILLS_FOR.get(use, ()):
            if sid not in out:
                out.append(sid)
    for sid in SKILLS_FOR_EVERYONE:
        if sid not in out:
            out.append(sid)
    return out


def save_skills(uses) -> None:
    """The starter skills for what they do on, the other starter skills
    off; their own skills are never touched. "Something else": all on."""
    try:
        from agents.skills import SkillLibrary
        lib = SkillLibrary()
        wanted = set(skills_for(uses))
        for skill in lib.all():
            if skill.source != "yours":
                lib.set_enabled(skill.id, not wanted or skill.id in wanted)
    except Exception:  # noqa: BLE001 - the skills are a nicety here, never a blocker
        _log.warning("could not set the starter skills", exc_info=True)


def _check_own_key(key: str) -> tuple[bool, str]:
    """Does this Gemini key work? Asked straight of Google, with the key."""
    import httpx
    try:
        r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                      params={"key": key}, timeout=12.0)
    except httpx.HTTPError:
        return False, "Couldn't reach Google - check the internet connection."
    if r.status_code == 200:
        return True, "Key works."
    return False, ("Google didn't accept that key. Copy it again in full from "
                   "aistudio.google.com/app/apikey.")


class SetupWizard(QDialog):
    """Sign in to HeyTora (or, without accounts, add a Gemini key)."""

    completed = pyqtSignal()                       # finished: access may have changed
    _result = pyqtSignal(str, bool, str)           # (job, ok, message) from a worker thread

    W = 440                                        # the card; the window is the card and its shadow
    SHADOW = 28

    def __init__(self, parent=None, *, account=None,
                 check_key: Callable[[str], tuple[bool, str]] = _check_own_key):
        super().__init__(parent)
        self.setWindowTitle("HeyTora")
        self.setModal(False)
        # Its own window in the taskbar (nothing else of HeyTora runs yet),
        # without Windows' frame: the card is the window.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if account is None:
            from account.service import account as _app_account
            account = _app_account()
        self._account = account
        from account import settings
        self._gated = settings.configured()        # HeyTora accounts: signing in is a must
        self._check_key = check_key
        self._connected = cfg.has_key()
        self._catcher = None                       # account.loopback.LinkCatcher, while signing in
        self._finished = False
        self._placed = False
        self._drag: Optional[QPoint] = None
        self._result.connect(self._on_result)
        self._build()
        self._refresh_connect()
        self._fit()

    @property
    def next_btn(self) -> QPushButton:
        """Finish: enabled once signed in."""
        return self.finish_btn

    def page(self) -> str:
        return "connect"

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        from account import settings
        from ui.app_widgets import wordmark
        from ui.onboarding_shape import stylesheet as tour_styles
        self.card = QWidget(self)
        self.card.setObjectName("signinCard")
        self.card.setStyleSheet(T.qss() + tour_styles() + _styles())
        self.card.setFixedWidth(self.W)
        body = QVBoxLayout(self.card)
        body.setContentsMargins(32, 18, 18, 26)
        body.setSpacing(0)

        top = QHBoxLayout()
        top.addWidget(wordmark(22), 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        # Before a sign-in, closing it closes HeyTora (ui/sign_in_gate.py).
        from PyQt6.QtGui import QIcon
        from ui.app_widgets import icon
        self.close_btn = QPushButton()
        self.close_btn.setIcon(QIcon(icon("x", T.TEXT_DIM, 16)))
        self.close_btn.setObjectName("signinClose")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("Close")
        self.close_btn.clicked.connect(self.close)
        top.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(top)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 14, 0)          # the margin the close button took on the right
        col.setSpacing(0)
        body.addLayout(col)
        col.addSpacing(22)
        title = QLabel("Sign in to HeyTora." if self._gated else "Connect to Gemini.")
        title.setObjectName("obTitle")
        col.addWidget(title)
        col.addSpacing(6)
        lines = QLabel("Continue in your browser with Google, Microsoft or your email. "
                       "New here? That makes your account." if self._gated else
                       "Tora thinks with Google's Gemini. Add your own Gemini key - "
                       "free from aistudio.google.com/app/apikey.")
        lines.setObjectName("obLines")
        lines.setWordWrap(True)
        col.addWidget(lines)
        col.addSpacing(22)
        self.connect_status = label("", "obNote", wrap=True)

        if settings.configured():
            # Sign-in happens on the website (Wispr's way): Google and
            # Microsoft only work properly in a real browser, the page can
            # stop bots, and the browser hands the sign-in back to this PC
            # (account/loopback.py). A new account is made the same way.
            self.browser_btn = button("Sign in with your browser  ↗", "primary")
            self.browser_btn.setMinimumHeight(tokens.BUTTON_H["lg"])
            self.browser_btn.clicked.connect(self._browser_sign_in)
            col.addWidget(self.browser_btn)
            self.reopen_btn = button("Open the sign-in page again", "link")
            self.reopen_btn.clicked.connect(self._open_sign_in_page)
            self.reopen_btn.hide()
            col.addWidget(self.reopen_btn, 0, Qt.AlignmentFlag.AlignHCenter)
            # Signed in, all of that falls away: who you are, the plan, and Finish.
            self.signed_in_box = QFrame()
            self.signed_in_box.setObjectName("signedIn")
            sl = QVBoxLayout(self.signed_in_box)
            sl.setContentsMargins(16, 14, 16, 12)
            sl.setSpacing(4)
            self.signed_in_as = label("", "cardTitle", wrap=True)
            sl.addWidget(self.signed_in_as)
            self.signed_in_plan = label("", "cardSub", wrap=True)
            sl.addWidget(self.signed_in_plan)
            switch = button("Use a different account", "link")
            switch.clicked.connect(self._different_account)
            sl.addWidget(switch, 0, Qt.AlignmentFlag.AlignLeft)
            self.signed_in_box.hide()
            col.addWidget(self.signed_in_box)
        else:
            self.browser_btn = None
            self.reopen_btn = None
            self.signed_in_box = None

        # The own key: after the account, never instead of it - one link,
        # opened only when wanted. Without accounts it is the whole window.
        col.addSpacing(6)
        self.key_link = button("Use my own Gemini key instead", "link")
        self.key_link.clicked.connect(self._open_key)
        self.key_link.hide()
        col.addWidget(self.key_link, 0, Qt.AlignmentFlag.AlignHCenter)
        self.key_card = QFrame()
        kl = QVBoxLayout(self.key_card)
        kl.setContentsMargins(0, 6, 0, 0)
        kl.setSpacing(6)
        if self._gated:
            kl.addWidget(label("Straight to Google, no plan limits. Free from "
                               "aistudio.google.com/app/apikey.", "obNote", wrap=True))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste your Gemini API key")
        self.key_btn = button("Use key", "ghost")
        self.key_btn.clicked.connect(self._use_key)
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(self.key_btn)
        kl.addLayout(key_row)
        self.key_card.setVisible(not self._gated)
        col.addWidget(self.key_card)
        col.addSpacing(10)
        col.addWidget(self.connect_status)
        col.addSpacing(16)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self.finish_btn = button("Finish", "primary")
        self.finish_btn.setMinimumWidth(140)
        self.finish_btn.clicked.connect(self._next)
        foot.addWidget(self.finish_btn)
        foot.addStretch(1)
        col.addLayout(foot)
        if self._gated and self._account.signed_in():
            self._show_signed_in(True, "")       # opened again, already signed in

    def _open_key(self) -> None:
        self.key_link.hide()
        self.key_card.show()
        self.key_edit.setFocus()
        self._fit()

    def _fit(self) -> None:
        """The window is the card, as tall as what it shows now, and its shadow."""
        self.connect_status.setVisible(bool(self.connect_status.text()))
        lay = self.card.layout()
        lay.invalidate()
        lay.activate()
        h = lay.totalHeightForWidth(self.W) if lay.hasHeightForWidth() else lay.sizeHint().height()
        self.card.setFixedHeight(h)
        self.card.move(self.SHADOW, self.SHADOW)
        self.setFixedSize(self.W + 2 * self.SHADOW, h + 2 * self.SHADOW)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._placed:                       # the middle of the screen, once
            self._placed = True
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                g = screen.availableGeometry()
                self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.card.geometry())
        kit.soft_shadow(p, r, tokens.RADIUS_XL, "hero")
        p.setBrush(QColor(T.BG))
        p.setPen(QPen(QColor(T.BORDER), 1.0) if T.is_dark() else Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, tokens.RADIUS_XL, tokens.RADIUS_XL)
        p.end()

    # No Windows frame: it moves by its background.
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None
        super().mouseReleaseEvent(e)

    # ── finishing ─────────────────────────────────────────────────────────────

    def _next(self) -> None:
        if self._gated and not self._account.signed_in():
            self.connect_status.setText("Sign in first. HeyTora needs your account.")
            return
        self._finish()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._stop_catcher()
        self.completed.emit()
        self.close()

    def closeEvent(self, event) -> None:
        self._stop_catcher()
        super().closeEvent(event)

    def reject(self) -> None:                       # the window's X, or the gate giving up
        self._stop_catcher()
        super().reject()

    def keyPressEvent(self, event) -> None:
        # Esc would close the window - and, before a sign-in, the app.
        if event.key() == Qt.Key.Key_Escape:
            return
        super().keyPressEvent(event)

    # ── connect ─────────────────────────────────────────────────────────────────

    def _refresh_connect(self) -> None:
        signed_in = self._account.signed_in()
        if signed_in and cfg.access_mode() == "account":
            self.connect_status.setText("")          # the signed-in card says who
        elif cfg.access_mode() == "own_key" and cfg.google_api_key and (signed_in or not self._gated):
            who = f"Signed in as {self._account.email}, using" if signed_in else "Using"
            self.connect_status.setText(f"{who} your own Gemini key.")
        else:
            self.connect_status.setText("")
        self._connect_locks()

    def _connect_locks(self) -> None:
        """Gated: no Finish, and no own key, until signed in."""
        if self._gated:
            signed_in = self._account.signed_in()
            self.finish_btn.setEnabled(signed_in)
            self.key_card.setEnabled(signed_in)
            self.key_link.setVisible(signed_in and self.key_card.isHidden())
        self._fit()

    def _run(self, job: str, work: Callable[[], str]) -> None:
        """Do `work` off the UI thread; its return (or error) comes back to _on_result."""
        def go():
            try:
                self._result.emit(job, True, work() or "")
            except Exception as e:  # noqa: BLE001 - shown to the user, in words
                self._result.emit(job, False, str(e))
        threading.Thread(target=go, daemon=True).start()

    def _browser_sign_in(self) -> None:
        """Wait on this PC for the browser, then open the website's sign-in
        page - it comes back here with a code only this app can redeem."""
        from account.service import AccountError
        if self._catcher is None:
            from account.loopback import LinkCatcher
            self._catcher = LinkCatcher(self._link_arrived)
        try:
            self._sign_in_url = self._account.browser_sign_in(self._catcher.start())
        except AccountError as e:
            self.connect_status.setText(str(e))
            return
        self._open_sign_in_page()
        self.reopen_btn.show()
        self.connect_status.setText("Finish signing in in your browser - this window signs you in "
                                    "by itself.")
        self._fit()

    def _show_signed_in(self, on: bool, plan: str) -> None:
        """Signed in: who, and the plan - the button and the "open again"
        link fall away, and the own key waits behind its link. Off: back again."""
        if self.signed_in_box is None:
            return
        self.signed_in_as.setText(f"You're signed in as {self._account.email}" if on else "")
        self.signed_in_plan.setText(plan)
        self.signed_in_plan.setVisible(bool(plan))
        self.signed_in_box.setVisible(on)
        self.browser_btn.setVisible(not on)
        self.reopen_btn.hide()
        self.key_card.hide()
        self.key_link.setVisible(on)
        if on:
            self.connect_status.setText("")
        self._fit()

    def _different_account(self) -> None:
        """Sign out here, and sign in again with another."""
        self._account.sign_out()
        self._connected = cfg.has_key()
        self._show_signed_in(False, "")
        self._connect_locks()

    def _open_sign_in_page(self) -> None:
        url = getattr(self, "_sign_in_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _link_arrived(self, code: str) -> tuple[bool, str]:
        """The email's link, clicked: runs on the catcher's thread; the browser shows the words."""
        try:
            summary = self._account.verify_link(code)
        except Exception as e:  # noqa: BLE001 - shown in the browser and here, in words
            self._result.emit("signin", False, str(e))
            return False, str(e)
        cfg.set_access_mode("account")
        welcome = self._claim_referral()
        if welcome:
            try:
                summary = self._account.summary(force=True)     # the 14 days are the plan now
            except Exception:  # noqa: BLE001 - the plan line can wait
                pass
        self._result.emit("signin", True, " ".join(w for w in (welcome, plan_line(summary)) if w))
        return True, "You can close this tab - HeyTora is ready."

    def _claim_referral(self) -> str:
        """A friend's invite, noted by the website for this sign-in (docs/
        REFERRALS.md): the 14 days start, and this says who they came from.
        "" when there was none - or it couldn't be used (Settings > Invite &
        Earn's Apply tab says why, if they try a code by hand)."""
        try:
            result = self._account.referral_claim()
        except Exception:  # noqa: BLE001 - an invite never stands in the way of signing in
            return ""
        if not result.get("ok"):
            return ""
        who = result.get("referrer_name") or "A friend"
        return (f"{who} gave you 14 days of Pro. Ask Tora 15 questions and "
                f"{'they get' if who == 'A friend' else who + ' gets'} a free month too.")

    def _stop_catcher(self) -> None:
        if self._catcher is not None:
            self._catcher.stop()

    def _use_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.connect_status.setText("Paste your key first.")
            return
        self.key_btn.setEnabled(False)
        self.connect_status.setText("Checking your key…")

        def work() -> str:
            ok, words = self._check_key(key)
            if not ok:
                raise RuntimeError(words)
            cfg.set_api_key("GOOGLE_API_KEY", key)
            cfg.set_access_mode("own_key")
            return words
        self._run("key", work)

    def _on_result(self, job: str, ok: bool, message: str) -> None:
        if job == "signin":
            if ok:
                self._connected = True
                self._stop_catcher()
                self._show_signed_in(True, message)
                self._connect_locks()
                from ui.single_instance import to_front
                to_front(self)                      # over the browser it was finished in
            else:
                self.connect_status.setText(message)
        elif job == "key":
            self.key_btn.setEnabled(True)
            if ok:
                self._connected = True
                self.key_edit.clear()
                self.connect_status.setText("Your key works. You're connected.")
            else:
                self.connect_status.setText(message)
        self._fit()


def _styles() -> str:
    return f"""
    QFrame#signedIn {{ background: {T.ACCENT_SOFT}; border: none; border-radius: {tokens.RADIUS_MD}px; }}
    QPushButton#signinClose {{ background: transparent; border: none; padding: 6px;
                               border-radius: {tokens.RADIUS_SM}px; }}
    QPushButton#signinClose:hover {{ background: {T.SURFACE_HOVER}; }}
    QLabel#obNote {{ color: {T.TEXT_DIM}; font-size: 12px; }}
    """


def plan_line(summary: dict) -> str:
    """"Free plan - 25 talk messages and 25 agent messages a month." from /me."""
    plan = (summary.get("plan") or "free").capitalize()
    limits = summary.get("limits") or {}
    parts = []
    if "talk" in limits:
        parts.append("unlimited talk" if limits["talk"] is None else f"{limits['talk']} talk messages")
    if "agent" in limits:
        parts.append("unlimited agent messages" if limits["agent"] is None
                     else f"{limits['agent']} agent messages")
    return f"{plan} plan" + (f" - {' and '.join(parts)} a month." if parts else ".")


def maybe_show_setup_wizard(parent=None) -> Optional[SetupWizard]:
    """Open the sign-in window on first run - and whenever Tora has no way to
    reach Gemini, however many times it has been launched: a silent
    do-nothing app is a worse answer than a window explaining why."""
    if setup_already_ran() and cfg.has_key():
        return None
    w = SetupWizard(parent)
    w.show()
    return w
