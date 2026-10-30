"""The shape: HeyTora's one home, grown out of the pill (DESIGN.md §1-§4).

A click on the resting pill (ui/flow_bar.py) grows it, beside the pill and
towards the middle of the screen, into a small app; the gear grows it again
into Settings. Escape steps back a size (Settings -> open -> the pill); a
click somewhere else, or the pill again, shrinks it away. Closing never
stops a job.

    pill ( ● )  ->  ┌ [Home][Insights][Jobs] ········ [Upgrade][⚙] ┐   open
                    │                 the page                     │
                    │ [Invite & earn]                     [Jobs +] │
                    └──────────────────────────────────────────────┘
                ->  ┌ [Home][Insights][Jobs] ············ [Upgrade][⚙] ┐   settings
                    │ Account      │                                  │
                    │ ...          │   the settings page              │
                    │ HeyTora 1.x  │                                  │
                    └─────────────────────────────────────────────────┘

The top bar is the same at both sizes, as HeyClicky's is: Home goes back
from Settings, the gear is lit while in it.

Every change of size is one move from rectangle `a` to rectangle `b` (on
the screen): pill -> open, open -> settings, settings -> open, and back to
the pill. The window is set once per move to cover both and stays still;
what grows is the shape painted inside it. Resizing a Windows window every
frame flickers; painting does not. The page fades in over the last part,
lifted 6 px into place.

Everything is here now (DESIGN.md §13, step 6: the old app window is
gone): the tabs at the open size; Settings, its sidebar and pages, at the
large one; and what the open shape is too small for - all of Insights,
everything asked - as a whole page at that size ("page"), with Back. A
job's details slide in over the Jobs tab.
"""
from __future__ import annotations

import ctypes
import sys
import time
from typing import Callable, Optional

from PyQt6.QtCore import QEasingCurve, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                             QStackedWidget, QVBoxLayout, QWidget)

from ui import app_theme as T
from ui import sounds
from ui import kit, tokens
from ui.app_shell import IconButton
from ui.app_widgets import Meter, button, label, later
from ui.flow_bar import place_card

OPEN_W, OPEN_H = 520, 420           # DESIGN.md §2
SETTINGS_W, SETTINGS_H = 820, 660
# A whole page - all of Insights, everything asked - at the old window's
# width, so Insights is laid out wide again (31 Oct: in the 820 px shape it
# was "ugly and squashed"). Smaller on a small screen; narrow below WIDE_MIN.
PAGE_W, PAGE_H = 1060, 700
WIDE_MIN = 1000
NAV_W = 196
PAD = 24                            # room around the shape in its window, for the shadow
LIFT = 6                            # the page rises this far as it fades in
TOP_BAR_H = 52
FOOT_H = 52
# The motion (DESIGN.md §2), measured against HeyClicky's frame by frame:
# the shape grows over the first GROW_PART of the time on a curve that is
# not all at the start (the site's curve did 60% of the growing in 30 ms -
# the first frame on screen was a big black box), and the page fades in from
# FADE_FROM, while the shape is finishing, not after it (an empty black
# panel for 80 ms read as a glitch).
GROW = QEasingCurve(QEasingCurve.Type.OutCubic)
FADE = QEasingCurve(QEasingCurve.Type.OutQuad)
GROW_PART = 0.55
FADE_FROM = 0.35
GHOST_PART = 0.4             # the old page fades out over this much of a change of size
CLOSE_FADE = 0.22            # closing: the page is gone by here (40 ms) -
CLOSE_SHRINK_FROM = CLOSE_FADE   # ... before the shape starts back to the pill, or it pokes out
FIRST_FRAME_MS = 16
JOB_W = 132                  # a running job's card beside the shape (DESIGN.md §3)
JOB_GAP = 10
MAX_JOBS = 3          # one frame: the window is on the screen before the clock starts
# A click outside closes the shape - and that click may be on the pill,
# which would open it again at once. A toggle this soon after is that click.
REOPEN_GUARD_S = 0.35

# (key, icon, name) - the tabs, left to right (_pages() builds them).
TABS = (
    ("home", "home", "Home"),
    ("chat", "chat", "Chat"),
    ("insights", "insights", "Insights"),
    ("agents", "agents", "Jobs"),
)

# The settings sidebar (DESIGN.md §4): (group, ((key, name, icon), ...)).
# Thirteen rows, as light as HeyClicky's eleven (heyclicky2.0.mp4: "ours is
# very, very full ... it feels very squashed"). A key of shape_settings.PAGES
# is one of its pages; any other is an app window settings tab; "invite" is
# the referral panel.
SETTINGS = (
    ("", (
        ("account", "Account", "circle-user-round"),
        ("system", "General", "monitor"),
        ("invite", "Invite & Earn", "gift"),
    )),
    ("TORA", (
        ("voice", "Voice", "megaphone"),
        ("microphone", "Microphone", "mic"),
        ("shortcuts", "Shortcuts", "keyboard"),
        ("cursor", "Cursor", "sparkles"),
        ("style", "Style", "graduation-cap"),
        ("memory", "Memory", "book-open"),
    )),
    ("WORK", (
        ("integrations", "Integrations", "link"),
        ("agents", "Agents", "bot"),
        ("routines", "Routines", "clock"),
        ("skills", "Skills", "blocks"),
    )),
)

# Pages one step in, with no row of their own: (name, the row lit while on
# them). Plans opens from Upgrade and Account's Change plan; Privacy and
# Advanced from rows at the foot of General; Code editors from Integrations;
# Limits & models from Agents.
INNER = {
    "plans": ("Plans", "account"),
    "privacy": ("Privacy", "system"),
    "advanced": ("Advanced", "system"),
    "devlink": ("Code editors", "integrations"),
    "agent-limits": ("Limits & models", "agents"),
}

# The grey line under each page's title, HeyClicky's way ("Your plan,
# usage, and account."): what the page is for, in a few words.
LEDES = {
    "account": "Your plan, this month's use, and your account.",
    "system": "How HeyTora behaves on your PC.",
    "invite": "Give a friend Pro for 14 days; get a free month.",
    "voice": "The voice Tora answers in, and how she answers.",
    "microphone": "Which microphone Tora listens through.",
    "shortcuts": "The keys that summon Tora.",
    "cursor": "The Tora cursor that lives on your screen.",
    "style": "How she teaches, and what she always keeps in mind.",
    "memory": "What Tora remembers about you, and where it came from.",
    "integrations": "Apps her jobs can reach into.",
    "agents": "Where agents work, and how much rope they get.",
    "routines": "Things Tora does on repeat, on this PC.",
    "skills": "How you like jobs done - and showing her how you work.",
    "plans": "Free, Pro and Max - and your billing.",
    "privacy": "What Tora looks at, and what she keeps.",
    "advanced": "Your own keys, the models in use, diagnostics.",
    "devlink": "Your code editors, read as data instead of screenshots.",
    "agent-limits": "What agents may do on their own, and how much one job may use.",
}


# The app window's settings tabs that step 5a split or folded in, for what
# still asks for them by name ("settings/usage" from a plan's note).
MOVED = {"general": "shortcuts", "usage": "account"}


def _pages() -> dict:
    from ui.shape_pages import ChatTab, HomeTab, InsightsTab, JobsTab
    return {"home": HomeTab, "chat": ChatTab, "insights": InsightsTab, "agents": JobsTab}


def _settings_kinds() -> dict:
    from ui.referral_window import invite_page
    from ui.settings_tabs import TABS as SETTINGS_TABS
    from ui.shape_settings import PAGES
    kinds = {key: cls for _sec, rows in SETTINGS_TABS for key, _w, _i, cls in rows}
    kinds.update(PAGES)
    kinds["invite"] = invite_page()
    return kinds


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


def _lerp(a: QRectF, b: QRectF, g: float) -> QRectF:
    return QRectF(a.x() + (b.x() - a.x()) * g, a.y() + (b.y() - a.y()) * g,
                  a.width() + (b.width() - a.width()) * g, a.height() + (b.height() - a.height()) * g)


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


class _NavRow(QAbstractButton):
    """A settings sidebar row, light as HeyClicky's: a small grey icon, 13 px
    words, a soft plate while chosen - not the app window's big rows."""

    H = 30
    ICON = 15

    def __init__(self, icon_name: str, text: str):
        super().__init__()
        self._icon, self._text = icon_name, text
        self.setCheckable(True)
        self.setFixedHeight(self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        self._hover = False

    def enterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, _e) -> None:
        from ui.app_widgets import icon
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        if self.isChecked() or self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(T.NAV_PLATE if self.isChecked() else T.NAV_HOVER))
            p.drawRoundedRect(r, tokens.RADIUS_SM, tokens.RADIUS_SM)
        ink = T.TEXT if self.isChecked() else T.TEXT_DIM
        y = (self.height() - self.ICON) // 2
        p.drawPixmap(10, y, icon(self._icon, ink, self.ICON, self.devicePixelRatioF()))
        p.setPen(QColor(ink))
        p.setFont(kit.font(13, 600 if self.isChecked() else 500))
        p.drawText(r.adjusted(10 + self.ICON + 10, 0, -6, 0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)
        p.end()


class _JobCard(QFrame):
    """A running job: "Working", its name, how far along. A click opens it."""

    def __init__(self, job: dict, open_key: Callable[[str], None]):
        super().__init__()
        self.setObjectName("jobCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._key = f"agents/job/{job['task_id']}" if job.get("task_id") else "agents"
        self._open = open_key
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(4)
        lay.addWidget(label("WORKING", "sectionTitle"))
        title = str(job.get("title") or "A job")
        name = label(title if len(title) <= 48 else title[:47].rstrip() + "…", "rowTitle", wrap=True)
        lay.addWidget(name)
        lay.addWidget(Meter(float(job.get("progress") or 0.0), height=3))
        self.setToolTip(str(job.get("doing") or title))

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._open(self._key)


class Shape(QWidget):
    """open_from(edge, pill) / show_settings(key) / back() / shrink() /
    toggle(edge, pill) / go(key). `bridge` is the app's AppBridge."""

    def __init__(self, bridge):
        super().__init__()
        self.bridge = bridge
        # Its bridge goes with it: what the bridge hears later (a plan read
        # in the background) can never reach a shape that is gone.
        if bridge.parent() is None:
            bridge.setParent(self)
        self.setWindowTitle("HeyTora")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.mode = "closed"                 # closed | open | settings | page
        self._edge = "bottom"
        self._pill = QRectF()                # global
        self._a = self._b = QRectF()         # the move: from a to b, global
        self._from_pill = True               # a (or, shrinking, the end) is the pill
        self._t = 0.0                        # 0 = at a, 1 = at b
        self._page_off = False               # closing before the page had faded in: keep it out
        self._ghost = None                   # the page going away, as a picture (see _move_to)
        self._ghost_at = QPointF()
        self._closed_at = 0.0
        self._anim: Optional[QVariantAnimation] = None
        self._pages: dict[str, QWidget] = {}
        self._tabs_built: dict[str, QWidget] = {}
        self._pending: set[str] = set()
        self._refresh = QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(150)       # one redraw per burst, as the app window
        self._refresh.timeout.connect(self._refresh_now)
        bridge.changed.connect(self._on_changed)

        self.body = QWidget(self)
        self.body.setObjectName("shapeBody")
        self.body.setStyleSheet(T.qss())
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_top_bar())
        self.views = QStackedWidget()
        self.views.addWidget(self._build_open_view())
        self.views.addWidget(self._build_settings_view())
        self.views.addWidget(self._build_page_view())
        lay.addWidget(self.views, 1)
        self._fade = QGraphicsOpacityEffect(self.body)
        self._fade.setOpacity(0.0)
        self.body.setGraphicsEffect(self._fade)
        self.body.hide()
        # Running jobs, beside the open shape - in the room the window has
        # for Settings anyway, so no window of their own.
        self.jobs = QWidget(self)
        self.jobs.setStyleSheet(T.qss())
        QVBoxLayout(self.jobs).setContentsMargins(0, 0, 0, 0)
        self.jobs.layout().setSpacing(JOB_GAP)
        self.jobs.hide()
        self.show_tab(TABS[0][0])

    # ── the top bar ──────────────────────────────────────────────────────────
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
        self.upgrade.clicked.connect(lambda: self.show_settings("plans"))
        lay.addWidget(self.upgrade)
        self.gear = _Tab("settings", "Settings")
        self.gear.clicked.connect(lambda: self.show_settings())
        group.addButton(self.gear)
        lay.addWidget(self.gear)
        return bar

    # ── the open size: a page and the foot ───────────────────────────────────
    def _build_open_view(self) -> QWidget:
        view = QWidget()
        lay = QVBoxLayout(view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        foot = QWidget()
        foot.setFixedHeight(FOOT_H)
        row = QHBoxLayout(foot)
        row.setContentsMargins(16, 0, 16, 12)
        invite = button("Invite && earn", "ghost")        # && - a lone & would underline the e
        invite.clicked.connect(lambda: self.show_settings("invite"))
        row.addWidget(invite)
        row.addStretch(1)
        jobs = button("New job +", "ghost")
        jobs.clicked.connect(self._new_job)
        row.addWidget(jobs)
        lay.addWidget(foot)
        return view

    # ── the page size: a whole page, and Back ────────────────────────────────
    # What the open shape is too small for - all of Insights, everything asked -
    # at the Settings size, without its sidebar (DESIGN.md §13, step 6: the
    # old app window is gone, so its pages live here).
    PAGES_WIDE = {"insights": ("InsightsPage", "Insights"), "journal": ("JournalPage", "Everything asked")}

    def _build_page_view(self) -> QWidget:
        view = QWidget()
        lay = QVBoxLayout(view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(16, 10, 16, 0)
        back = button("‹ Back", "link")
        back.clicked.connect(lambda: self._move_to("open"))
        top.addWidget(back)
        top.addStretch(1)
        lay.addLayout(top)
        self.page_stack = QStackedWidget()
        lay.addWidget(self.page_stack, 1)
        self._wide_built: dict[str, QWidget] = {}
        return view

    def show_page(self, key: str) -> None:
        """A whole page (PAGES_WIDE) at its own size (PAGE_W), built the
        first time it is asked for."""
        if self.mode == "closed" or key not in self.PAGES_WIDE:
            return
        page = self._wide_built.get(key)
        if page is None:
            from ui import app_pages
            cls_name, heading = self.PAGES_WIDE[key]
            cls = getattr(app_pages, cls_name)
            narrow = self._rect("page").width() < WIDE_MIN
            page = type(cls_name, (cls,), {"HEADING": heading, "NARROW": narrow})(self.bridge)
            page.navigate.connect(self._navigate)
            self._wide_built[key] = page
            self.page_stack.addWidget(page)
            self.body.setStyleSheet(T.qss())
        else:
            page.refresh()
        self.page_stack.setCurrentWidget(page)
        self._move_to("page")

    def open_job(self, task_id: str) -> None:
        """A job's details: the Jobs tab, its drawer sliding in over the
        shape - what "Open job" and a running job's card open."""
        if self.mode == "closed" or not task_id:
            return
        self.show_tab("agents")
        from ui.agents_page import JobDrawer
        if getattr(self, "_job_drawer", None) is None:
            self._job_drawer = JobDrawer(self._pages["agents"], self.bridge, host=self.body)
        self._job_drawer.show_job(task_id)

    # ── the settings size: the sidebar and a tab ─────────────────────────────
    def _build_settings_view(self) -> QWidget:
        view = QWidget()
        # Small dialogs from a tab (Change shortcut, Sign out?, Delete
        # account) sit over `window()._settings` when it shows (settings_tabs).
        self._settings = view
        lay = QHBoxLayout(view)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        nav = QWidget()
        nav.setFixedWidth(NAV_W)
        col = QVBoxLayout(nav)
        col.setContentsMargins(10, 4, 10, 14)
        col.setSpacing(1)
        self._rows: dict[str, _NavRow] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for heading, rows in SETTINGS:
            if heading:
                col.addSpacing(12)
                head = label(heading, "sectionTitle")
                head.setContentsMargins(10, 0, 0, 4)
                col.addWidget(head)
            for key, name, icon_name in rows:
                row = _NavRow(icon_name, name)
                row.clicked.connect(lambda _c=False, k=key: self.select(k))
                group.addButton(row)
                col.addWidget(row)
                self._rows[key] = row
        col.addStretch(1)
        self._version = label("", "faint")
        self._version.setContentsMargins(12, 0, 0, 0)
        col.addWidget(self._version)
        lay.addWidget(nav)
        rule = QFrame()
        rule.setObjectName("vr")
        rule.setFixedWidth(1)
        lay.addWidget(rule)
        self.settings_stack = QStackedWidget()
        lay.addWidget(self.settings_stack, 1)
        return view

    def select(self, key: str) -> None:
        """A settings page, built the first time it is asked for."""
        kinds = _settings_kinds()
        key = MOVED.get(key, key)
        if key not in kinds or (key not in self._rows and key not in INNER):
            key = "account"
        page = self._tabs_built.get(key)
        if page is None:
            # Titled as its row is named here (not the app window's tab
            # names), so the two never disagree; its line under the title.
            name = INNER[key][0] if key in INNER else next(
                n for _h, rows in SETTINGS for k, n, _i in rows if k == key)
            cls = kinds[key]
            page = type(cls.__name__, (cls,), {"HEADING": name})(self.bridge)
            if hasattr(page, "body"):
                # Title and its line close together, near the top, as
                # HeyClicky's: the app window's pages open 34 px down.
                head = page.body.top.itemAt(0).widget() if page.body.top.count() else None
                if head is not None and head.layout() is not None:
                    head.layout().setContentsMargins(0, 18, 0, 0)
                if key in LEDES:
                    lede = label(LEDES[key], "pageLede", wrap=True)
                    lede.setContentsMargins(0, 0, 0, 6)
                    page.body.top.addWidget(lede)
            page.navigate.connect(self._navigate)
            self._tabs_built[key] = page
            self.settings_stack.addWidget(page)
            self.body.setStyleSheet(T.qss())    # polish the new page's labels at the right size
        else:
            page.refresh()
        self.settings_stack.setCurrentWidget(page)
        self._rows[INNER[key][1] if key in INNER else key].setChecked(True)

    def current_setting(self) -> str:
        page = self.settings_stack.currentWidget()
        return next((k for k, w in self._tabs_built.items() if w is page), "")

    def _version_text(self) -> None:
        try:
            v = self.bridge.version()
        except Exception:  # noqa: BLE001
            v = ""
        self._version.setText(f"HeyTora {v}" if v else "HeyTora")

    # ── pages ────────────────────────────────────────────────────────────────
    def show_tab(self, key: str) -> None:
        kinds = _pages()
        if key not in kinds:
            self._navigate(key)               # not a tab: wherever it lives
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
        if self.mode in ("settings", "page"):
            self._move_to("open")

    def _upgrade_shown(self) -> None:
        """Upgrade, unless there is nothing to upgrade to (Max), or no plan
        to upgrade (their own Gemini key)."""
        try:
            st = self.bridge.account_state() or {}
        except Exception:  # noqa: BLE001 - shown: Plans says what applies
            st = {}
        plan = ((st.get("summary") or {}).get("plan") or "").lower() if st.get("signed_in") else ""
        self.upgrade.setVisible(plan != "max" and st.get("mode") != "own_key")

    def _new_job(self) -> None:
        """New job +: the Jobs tab, its box ready to type in."""
        self.show_tab("agents")
        page = self._pages.get("agents")
        if page is not None:
            page.focus_job_box()

    def _check_tabs(self) -> None:
        """Light the tab for where the shape is: the gear in Settings."""
        if self.mode == "settings":
            self.gear.setChecked(True)
        elif self._current():
            self._tabs[self._current()].setChecked(True)

    def _current(self) -> str:
        page = self.stack.currentWidget()
        return next((k for k, w in self._pages.items() if w is page), "")

    def go(self, key: str) -> None:
        """Open at `key`: a tab ("agents"), a setting ("settings/voice"), a
        whole page ("insights/all", "journal"), a job ("agents/job/<id>")."""
        self._navigate(key)

    def _navigate(self, key: str) -> None:
        key = key[len("app/"):] if key.startswith("app/") else key    # the old window's names
        if key == "talk" or key.startswith("ask:"):
            # Out of the picture first: she looks at the screen - theirs, not
            # this one - and its window must be gone from the front for that.
            text = key[len("ask:"):]
            self.shrink()
            later(self, (lambda: self.bridge.ask(text)) if text else self.bridge.talk,
                  tokens.SHAPE_CLOSE + 60)
        elif key.startswith("agents/job/"):
            self.open_job(key.split("/", 2)[2])
        elif key in ("insights/all", "journal"):
            self.show_page("insights" if key == "insights/all" else "journal")
        elif key in _pages():
            self.show_tab(key)
        elif key.startswith("settings"):
            self.show_settings(key.partition("/")[2] or "account")
        elif key in _settings_kinds() or key in MOVED or key in ("skills", "setup"):
            self.show_settings({"skills": "skills", "setup": "advanced"}.get(key, key))
        else:
            self.show_tab("home")                        # nothing by that name: Home

    def _on_changed(self, topic: str) -> None:
        self._pending.add(topic)
        self._refresh.start()

    def _refresh_now(self) -> None:
        """Redraw the page on show if it is live and shows what changed (Page.TOPICS)."""
        if not self.isVisible():
            return
        topics, self._pending = self._pending, set()
        if "account" in topics:
            self._upgrade_shown()
        if "agents" in topics or "state" in topics:
            self._place_jobs()
        page = {"settings": self.settings_stack, "page": self.page_stack}.get(self.mode, self.stack).currentWidget()
        if page is not None and getattr(page, "LIVE", False) and set(getattr(page, "TOPICS", ())) & topics:
            partial = getattr(page, "refresh_topics", None)
            partial(topics) if partial is not None else page.refresh()

    def retheme(self) -> None:
        self.body.setStyleSheet(T.qss())
        for stack in (self.stack, self.settings_stack):
            page = stack.currentWidget()
            if page is not None:
                page.refresh()
        for w in self.findChildren(QWidget):
            w.update()
        self.update()

    # ── sizes ────────────────────────────────────────────────────────────────
    def is_open(self) -> bool:
        return self.mode != "closed"

    def _rect(self, mode: str) -> QRectF:
        """Where `mode` sits on the screen: beside the pill, towards the middle."""
        if mode == "closed":
            return QRectF(self._pill)
        screen = QGuiApplication.screenAt(self._pill.center().toPoint()) or QGuiApplication.primaryScreen()
        if mode == "page":
            room = screen.availableGeometry()
            w, h = min(PAGE_W, room.width() - 48), min(PAGE_H, room.height() - 48)
        else:
            w, h = (SETTINGS_W, SETTINGS_H) if mode == "settings" else (OPEN_W, OPEN_H)
        x, y = place_card(self._edge, self._pill, screen.availableGeometry(), w, h)
        return QRectF(round(x), round(y), w, h)

    def toggle(self, edge: str, pill: QRectF) -> None:
        if self.is_open():
            self.shrink()
        elif time.monotonic() - self._closed_at > REOPEN_GUARD_S:
            self.open_from(edge, pill)

    def open_from(self, edge: str, pill: QRectF) -> None:
        """Grow out of `pill` (global coordinates), docked on `edge`.

        The window is made big enough for every size the shape can take
        from this pill - the pill, open, Settings - and does not move again
        until it is closed: a Windows window moved or resized mid-animation
        shows one frame of its old picture in its new place (the page
        jumped 30 px on close, 28 Oct). Its see-through parts let clicks
        through to what is under them."""
        self._edge, self._pill = edge, QRectF(pill)
        self._upgrade_shown()
        span = self._pill.united(self._rect("open")).united(self._rect("settings")).united(self._rect("page"))
        self.setGeometry(span.adjusted(-PAD, -PAD, PAD, PAD).toRect())
        self.mode = "open"
        self._page_off = False
        self._a, self._b, self._from_pill = QRectF(self._pill), self._rect("open"), True
        self.views.setCurrentIndex(0)
        self._check_tabs()
        self.body.setGeometry(self._local(self._b).toRect())
        self._step(0.0)                      # the pill, the page hidden
        page = self.stack.currentWidget()
        if page is not None and getattr(page, "LIVE", False):
            page.refresh()
        self.show()
        self.raise_()
        self.activateWindow()
        sounds.ui("open")
        if not animations_on():
            self._run(1.0, tokens.SHAPE_OPEN)
            return
        # The clock starts once the window is on the screen: started at
        # show(), the first frame Windows drew was already a third grown.
        later(self, self._start_open, FIRST_FRAME_MS)

    def _start_open(self) -> None:
        if self.mode == "open" and self._t == 0.0 and self._from_pill:
            self._run(1.0, tokens.SHAPE_OPEN)

    def open_to_type(self, edge: str, pill: QRectF) -> None:
        """Ctrl, Ctrl: open (if it is not) on Chat, its box ready to type in."""
        if not self.is_open():
            self.open_from(edge, pill)
        self.show_tab("chat")
        self.raise_()
        self.activateWindow()
        page = self._pages.get("chat")
        if page is not None:
            later(page.ask_box, page.ask_box.setFocus)

    def show_settings(self, key: str = "account") -> None:
        if self.mode == "closed":
            return
        self._version_text()
        if self.mode != "settings":
            sounds.ui("settings")
        self._move_to("settings", key)

    def back(self) -> None:
        """Escape: Settings (or a whole page) -> open -> the pill."""
        if self.mode in ("settings", "page"):
            self._move_to("open")
        else:
            self.shrink()

    def shrink(self) -> None:
        """Back into the pill."""
        if self.mode == "closed":
            return
        self._closed_at = time.monotonic()
        self._move_to("closed")

    def _move_to(self, mode: str, setting: str = "") -> None:
        """One move: from where the shape is now to `mode`'s rectangle."""
        was = self.mode
        if mode == was and self._t >= 1.0 and not setting:
            return
        now = self._now_rect()
        self.mode = mode
        if self._anim is not None:
            self._anim.stop()
        if mode == "closed":
            # Shrinking is growing backwards, from the pill to here: t 1 -> 0.
            # The page fades out first, where it is - unless it had not
            # finished fading in, when it stays out (no flash back in).
            self._page_off = self._fade.opacity() < 1.0
            self._a, self._b, self._from_pill = QRectF(self._pill), now, True
            self._t = 1.0
            self._check_tabs()
            self._step(1.0)
            self._run(0.0, tokens.SHAPE_CLOSE)
            return
        self._page_off = False
        if mode == was:
            # Another settings page at the same size: no move.
            if setting:
                self.select(setting)
            return
        # The page going away fades out as a picture while the shape grows
        # (GHOST_PART), so the shape is never an empty black box between the
        # two pages (it was for 80 ms on the way into Settings).
        self._ghost = self._picture_of_page() if self.body.isVisible() else None
        self._ghost_at = QPointF(self.body.pos())
        # Off the screen before anything changes under it: swapped first, the
        # Settings page showed full size in the small shape for a frame.
        self._fade.setEnabled(True)
        self._fade.setOpacity(0.0)
        self.body.hide()
        self._a, self._b, self._from_pill = now, self._rect(mode), False
        self._t = 0.0
        self.views.setCurrentIndex({"settings": 1, "page": 2}.get(mode, 0))
        if setting:
            self.select(setting)
        self._check_tabs()
        self.body.setGeometry(self._local(self._b).toRect())
        self._step(0.0)
        self._run(1.0, tokens.SHAPE_OPEN)

    def _picture_of_page(self) -> QPixmap:
        """The page as it is now, on a clear ground - grab() fills what the
        page leaves unpainted with the light palette, a grey flash in dark."""
        dpr = self.devicePixelRatioF()
        pix = QPixmap(QSize(round(self.body.width() * dpr), round(self.body.height() * dpr)))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        self.body.render(pix, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        return pix

    def _now_rect(self) -> QRectF:
        if self._a.isNull() and self._b.isNull():
            return QRectF(self._pill)
        return _lerp(self._a, self._b, self._grow())

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

    def _grow(self) -> float:
        if self.mode == "closed":
            # Closing has its own timing, not opening's played backwards (the
            # page sat still for 80 ms first): the page fades at once, and the
            # shape starts back to the pill a moment after.
            done = 1.0 - self._t
            return 1.0 - GROW.valueForProgress(_part(done, CLOSE_SHRINK_FROM, 1.0 - CLOSE_SHRINK_FROM))
        return GROW.valueForProgress(_part(self._t, 0.0, GROW_PART))

    def _page(self, t: float) -> float:
        """How much of the page shows at `t`, 0-1."""
        if self._page_off:
            return 0.0
        if self.mode == "closed":
            return 1.0 - _part(1.0 - t, 0.0, CLOSE_FADE)
        return FADE.valueForProgress(_part(t, FADE_FROM, 1.0 - FADE_FROM))

    def _step(self, t: float) -> None:
        self._t = t
        fade = self._page(t)
        if self.mode == "closed" or t >= GHOST_PART:
            self._ghost = None
        self._fade.setOpacity(fade)
        # Only while fading: an effect draws the page through a pixmap, which
        # can soften text on a scaled screen.
        self._fade.setEnabled(fade < 1.0)
        self.body.setVisible(fade > 0.0)
        full = self._local(self._b).toRect()
        self.body.move(full.x(), full.y() + round(LIFT * (1.0 - fade)))
        self.update()
        if t >= 1.0 and self.mode == "open":
            self._place_jobs()
        elif self.jobs.isVisible():
            self.jobs.hide()
        if t <= 0.0 and self.mode == "closed":
            self.hide()

    # ── running jobs, beside the shape ───────────────────────────────────────
    def _place_jobs(self) -> None:
        """Up to MAX_JOBS cards beside the open shape, on the side away from
        the pill's edge where there is room; none while it moves or is
        another size, or when nothing runs."""
        running = []
        if self.mode == "open" and self._t >= 1.0:
            try:
                running = list(self.bridge.agents())[:MAX_JOBS]
            except Exception:  # noqa: BLE001 - no cards, the shape is fine
                running = []
        lay = self.jobs.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not running:
            self.jobs.hide()
            return
        for job in running:
            card = _JobCard(job, self._navigate)
            lay.addWidget(card)
            # Added to a holder already on the screen, a card is shown only
            # later - and until then measures 0 px, so the cards did too.
            card.show()
        shape = self._local(self._b)
        room_left = shape.left() - JOB_GAP - JOB_W >= 0
        room_right = shape.right() + JOB_GAP + JOB_W <= self.width()
        prefer_left = self._edge != "left"
        if (prefer_left and room_left) or not room_right:
            if not room_left:
                self.jobs.hide()
                return
            x = shape.left() - JOB_GAP - JOB_W
        else:
            x = shape.right() + JOB_GAP
        # Measured for the cards' width: the names wrap, and adjustSize() on
        # cards just added came out 0 px tall.
        lay.activate()
        tall = lay.heightForWidth(JOB_W) if lay.hasHeightForWidth() else lay.sizeHint().height()
        self.jobs.setGeometry(round(x), round(shape.top() + TOP_BAR_H), JOB_W, max(tall, lay.sizeHint().height()))
        self.jobs.show()
        self.jobs.raise_()

    def _local(self, r: QRectF) -> QRectF:
        return r.translated(-self.x(), -self.y())

    def _full(self) -> QRectF:
        """Where the shape ends up, in this window."""
        return self._local(self._b)

    def shape_rect(self) -> QRectF:
        """Where the shape is painted now, in this window."""
        return self._local(self._now_rect())

    # ── painting and keys ────────────────────────────────────────────────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.shape_rect()
        # How much of the pill is left: all of it at the pill, none once grown.
        g = self._grow() if self._from_pill else 1.0
        radius = min(r.height(), r.width()) / 2 * (1 - g) + tokens.RADIUS_LG * g
        kit.soft_shadow(p, r, radius, "float", strength=g)
        p.setBrush(kit.mix(tokens.PILL, T.BG, g))
        p.setPen(QPen(QColor(T.BORDER), 1.0) if T.is_dark() else Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, radius, radius)
        if self._ghost is not None:
            clip = QPainterPath()
            clip.addRoundedRect(r, radius, radius)
            p.setClipPath(clip)
            p.setOpacity(1.0 - _part(self._t, 0.0, GHOST_PART))
            p.drawPixmap(self._ghost_at, self._ghost)
        p.end()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.back()
            return
        super().keyPressEvent(e)

    def changeEvent(self, e) -> None:
        # A click somewhere else: the shape goes, as a menu does.
        if e.type() == e.Type.ActivationChange and not self.isActiveWindow() and self.is_open():
            later(self, self._left)
        super().changeEvent(e)

    def _left(self) -> None:
        """Focus went elsewhere - unless to something of the shape's own (a
        folder picker, Edit what Tora remembers) or a dropdown's list."""
        if self.isActiveWindow() or not self.is_open():
            return
        app = QApplication.instance()
        if app is not None and (app.activeModalWidget() is not None or app.activePopupWidget() is not None):
            return
        w = app.activeWindow() if app is not None else None
        while w is not None:
            if w is self:
                return
            w = w.parentWidget()
        self.shrink()
