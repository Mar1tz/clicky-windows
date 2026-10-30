"""The shape (28 Oct 2026, DESIGN.md §1-§3): a click on the resting pill
grows it into the app, beside the pill and towards the middle of the
screen; Escape, a click elsewhere or the pill again shrinks it back.

"Copy HeyClicky's pages ... it opens up nice, smoothly" - with our colours,
light and dark, and movable, "as long as it doesn't result in anything
breaking": the app window stays, and what the shape has no page for yet
opens there.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def shape(qapp, monkeypatch):
    from ui import shape as S
    from ui.app_bridge import AppBridge
    monkeypatch.setattr(S, "animations_on", lambda: False)     # every step lands at once
    opened = []                                  # the jobs it was asked to open
    real = S.Shape.open_job

    def open_job(self, task_id):                 # on the class: no loop back to the shape
        opened.append(f"agents/job/{task_id}")
        real(self, task_id)
    monkeypatch.setattr(S.Shape, "open_job", open_job)
    s = S.Shape(AppBridge())
    s.opened = opened
    yield s
    from PyQt6.QtCore import QCoreApplication, QEvent
    s.hide()
    s.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)   # gone before the next test


def _pill_at(edge: str):
    """A pill on `edge` of the primary screen, as the flow bar reports it."""
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QGuiApplication
    g = QGuiApplication.primaryScreen().availableGeometry()
    cx, cy = g.center().x(), g.center().y()
    return {
        "bottom": QRectF(cx - 23, g.bottom() - 22, 46, 10),
        "top": QRectF(cx - 23, g.top() + 12, 46, 10),
        "left": QRectF(g.left() + 12, cy - 23, 10, 46),
        "right": QRectF(g.right() - 22, cy - 23, 10, 46),
    }[edge]


# ── the pill hands its click to the shape ─────────────────────────────────

def test_a_click_opens_the_shape_once_it_is_wired(qapp):
    from ui.flow_bar import FlowBar
    bar = FlowBar()
    asked = []
    bar.talk_requested.connect(lambda: asked.append("talk"))
    bar.open_requested.connect(lambda: asked.append("open"))
    bar._click(bar.pill.pill_rect().center())
    assert asked == ["talk"], "not wired: a click talks, as before"
    bar.click_opens = True
    bar._click(bar.pill.pill_rect().center())
    assert asked == ["talk", "open"]
    edge, rect = bar.pill_geometry()
    assert edge == "bottom" and rect.width() > rect.height()
    bar.close()


# ── growing and shrinking ──────────────────────────────────────────────────

@pytest.mark.parametrize("edge", ["bottom", "top", "left", "right"])
def test_it_grows_towards_the_middle_of_the_screen(shape, edge):
    from ui import shape as S
    pill = _pill_at(edge)
    shape.open_from(edge, pill)
    body = shape._full().translated(shape.x(), shape.y())
    assert shape.is_open() and shape.isVisible()
    if edge == "bottom":
        assert body.bottom() < pill.top()
    elif edge == "top":
        assert body.top() > pill.bottom()
    elif edge == "left":
        assert body.left() > pill.right()
    else:
        assert body.right() < pill.left()
    assert (body.width(), body.height()) == (S.OPEN_W, S.OPEN_H)


def test_it_starts_as_the_pill_and_ends_the_full_size(shape):
    pill = _pill_at("bottom")
    shape.open_from("bottom", pill)
    shape._step(0.0)
    start = shape.shape_rect().translated(shape.x(), shape.y())
    assert abs(start.center().x() - pill.center().x()) < 1 and abs(start.width() - pill.width()) < 1
    assert not shape.body.isVisible(), "the page waits until the shape has grown"
    shape._step(1.0)
    assert shape.shape_rect() == shape._full()
    assert shape.body.isVisible() and shape._fade.opacity() == 1.0


def test_escape_and_the_pill_again_shrink_it(shape, qapp):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    shape.open_from("bottom", _pill_at("bottom"))
    shape.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not shape.is_open() and not shape.isVisible()
    shape._closed_at = time.monotonic() - 1
    shape.toggle("bottom", _pill_at("bottom"))
    shape.show_settings()
    shape.toggle("bottom", _pill_at("bottom"))
    assert not shape.is_open(), "the pill shrinks it from Settings too"
    shape._closed_at = time.monotonic() - 1
    shape.toggle("bottom", _pill_at("bottom"))
    assert shape.is_open()
    shape.toggle("bottom", _pill_at("bottom"))
    assert not shape.is_open()


def test_the_click_that_closed_it_does_not_open_it_again(shape):
    """A click outside closes it - and that click may be on the pill."""
    shape.open_from("bottom", _pill_at("bottom"))
    shape.shrink()
    shape.toggle("bottom", _pill_at("bottom"))
    assert not shape.is_open()


# ── what is in it ──────────────────────────────────────────────────────────

def test_the_tabs_are_its_own_and_all_of_insights_is_a_whole_page_in_it(shape):
    from ui.app_pages import InsightsPage
    from ui.shape_pages import ChatTab, HomeTab, InsightsTab
    assert isinstance(shape.stack.currentWidget(), HomeTab)
    shape.open_from("bottom", _pill_at("bottom"))
    for key, kind in (("chat", ChatTab), ("insights", InsightsTab), ("home", HomeTab)):
        shape.show_tab(key)
        assert isinstance(shape.stack.currentWidget(), kind) and shape._tabs[key].isChecked()
    shape.show_tab("insights")
    shape.stack.currentWidget().navigate.emit("insights/all")      # "All of Insights"
    assert shape.mode == "page" and isinstance(shape.page_stack.currentWidget(), InsightsPage)
    assert shape._full().width() == shape._rect("settings").width(), "the large size"
    shape.back()
    assert shape.mode == "open", "Back, or Escape: the tabs again"


def test_everything_asked_is_a_whole_page_too(shape):
    from ui.app_pages import JournalPage
    shape.open_from("bottom", _pill_at("bottom"))
    shape.go("journal")
    assert shape.mode == "page" and isinstance(shape.page_stack.currentWidget(), JournalPage)


def test_a_jobs_details_slide_in_over_the_shape(shape):
    shape.open_from("bottom", _pill_at("bottom"))
    shape.go("agents/job/t9")
    drawer = shape._job_drawer
    assert shape._current() == "agents" and drawer.isVisible()
    assert drawer.parentWidget() is shape.body, "over what is painted, not the bigger window"


def test_the_old_windows_addresses_land_in_the_shape(shape):
    shape.open_from("bottom", _pill_at("bottom"))
    shape.go("app/agents/job/t3")
    assert shape.opened[-1] == "agents/job/t3"
    shape.go("settings/voice")
    assert shape.mode == "settings" and shape.current_setting() == "voice"
    shape.go("invite")
    assert shape.current_setting() == "invite"
    shape.go("something-gone")
    assert shape.mode == "open" and shape._current() == "home", "nothing by that name: Home"


def test_talk_tucks_the_shape_away_then_listens(shape, qapp, monkeypatch):
    """She looks at the screen: the shape would be in the picture."""
    from PyQt6.QtCore import QTimer
    asked = []
    monkeypatch.setattr(shape.bridge, "talk", lambda: asked.append("talk"))
    later = []
    import ui.shape as _S
    monkeypatch.setattr(_S, "later", lambda owner, fn, ms=0: later.append(fn))
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_tab("chat")
    shape.stack.currentWidget().navigate.emit("talk")
    assert not shape.is_open() and asked == []
    for fn in later:
        fn()
    assert asked == ["talk"]


def test_the_chat_reads_oldest_first():
    from ui.shape_pages import conversation
    rows = [{"question": "Second?", "answer": "Two."}, {"question": "First?", "answer": ""},
            {"question": "  ", "answer": "dropped"}]
    assert conversation(rows) == [("asked", "First?"), ("asked", "Second?"), ("answer", "Two.")]


def test_the_insight_tiles_are_only_what_was_measured():
    from ui.shape_pages import tiles
    data = {"time": {"total_min": 75, "this_week_min": 12},
            "handled": {"total": 9, "ask": 6, "show": 2, "do": 1},
            "knows": {"facts": 4, "documents": 1}, "streak": 1, "longest": 5}
    assert tiles(data) == [
        ("TIME TORA WORKED", "1h 15m", "12 min this week"),
        ("THINGS HANDLED", "9", "6 asked · 2 shown · 1 done"),
        ("WHAT SHE KNOWS", "4", "facts · 1 document"),
        ("STREAK", "1 day", "longest 5"),
    ]
    assert tiles({})[0][1] == "0 min", "nothing yet is zero, not an error"


def test_running_jobs_sit_beside_the_open_shape(shape, monkeypatch):
    jobs = [{"task_id": "t1", "title": "Tidy my downloads", "doing": "Moving files", "progress": 0.4}]
    monkeypatch.setattr(shape.bridge, "agents", lambda: jobs)
    shape.open_from("bottom", _pill_at("bottom"))
    assert shape.jobs.isVisible()
    shape._place_jobs()                          # again, as a refresh does: the holder is showing now
    assert shape.jobs.height() > 40, "the cards are as tall as they need"
    body = shape._full()
    assert shape.jobs.geometry().right() < body.left() or shape.jobs.geometry().left() > body.right()
    card = shape.jobs.layout().itemAt(0).widget()
    card._open(card._key)
    assert shape.opened == ["agents/job/t1"]


def test_no_job_cards_while_moving_in_settings_or_with_nothing_running(shape, monkeypatch):
    jobs = [{"task_id": "t1", "title": "A job", "progress": 0.1}]
    monkeypatch.setattr(shape.bridge, "agents", lambda: jobs)
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings()
    assert not shape.jobs.isVisible()
    shape.back()
    assert shape.jobs.isVisible()
    jobs.clear()
    shape._place_jobs()
    assert not shape.jobs.isVisible()


# ── Settings, inside it ────────────────────────────────────────────────────

def test_the_gear_grows_it_into_settings_and_escape_steps_back(shape):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from ui import shape as S
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    pill = _pill_at("bottom")
    shape.open_from("bottom", pill)
    shape.gear.click()
    assert shape.mode == "settings" and shape.gear.isChecked()
    body = shape._full().translated(shape.x(), shape.y())
    assert (body.width(), body.height()) == (S.SETTINGS_W, S.SETTINGS_H)
    assert body.bottom() < pill.top(), "still beside the pill"
    assert shape.current_setting() == "account"
    shape.keyPressEvent(esc)
    assert shape.mode == "open" and shape._tabs["home"].isChecked()
    assert (shape._full().width(), shape._full().height()) == (S.OPEN_W, S.OPEN_H)
    shape.keyPressEvent(esc)
    assert shape.mode == "closed" and not shape.isVisible()


def test_upgrade_opens_plans_and_home_comes_back(shape):
    shape.open_from("bottom", _pill_at("bottom"))
    shape.upgrade.click()
    assert shape.mode == "settings" and shape.current_setting() == "plans"
    assert shape.opened == [], "Plans is in the shape now, not the app window"
    shape._tabs["home"].click()
    assert shape.mode == "open"


def test_every_settings_row_is_a_page_titled_as_its_row(shape, qapp):
    """Measured in the app's own font, as main.py loads it: the fallback
    face is wider, and a model name like gemini-3.1-flash-tts-preview
    alone takes 364px in it."""
    from ui import shape as S
    from ui import tokens
    tokens.load_fonts()
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings()
    for _heading, rows in S.SETTINGS:
        for key, name, _icon in rows:
            if key == "invite":
                continue
            shape.select(key)
            qapp.processEvents()
            page = shape.settings_stack.currentWidget()
            assert shape.current_setting() == key and page.HEADING == name
            inner = page.body.widget().minimumSizeHint().width()
            assert inner <= S.SETTINGS_W - S.NAV_W, f"{key} needs {inner}px"


def _wait(qapp, until, s=3.0):
    import time as _t
    end = _t.monotonic() + s
    while not until() and _t.monotonic() < end:
        qapp.processEvents()
        _t.sleep(0.01)
    return until()


def test_invite_and_earn_is_the_referral_panel_as_a_settings_page(shape, qapp, monkeypatch):
    """The owner kept our referral design - the gift card, how it works,
    Refer / Past referrals / Apply referral - over HeyClicky's (29 Oct)."""
    from PyQt6.QtGui import QGuiApplication
    from ui.referral_window import GiftCard
    st = {"code": "DYLANH4821", "link": "heytora.co.za/r/DYLANH4821", "friends": [
        {"name": "Sam", "status": "joined", "asked": 6}], "months_waiting": 1, "months_used": 0,
        "new_account": False}
    monkeypatch.setattr(shape.bridge, "referral_status", lambda: dict(st))
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings("invite")
    assert shape.mode == "settings" and shape.opened == [], "in the shape, not a window of its own"
    page = shape.settings_stack.currentWidget()
    assert page.HEADING == "Invite & Earn"
    panel = page.panel
    assert _wait(qapp, lambda: panel._state is not None), "loaded off the UI thread"
    assert page.findChild(GiftCard) is not None, "the gift card"
    assert panel.link_edit.text() == "heytora.co.za/r/DYLANH4821"
    panel.copy_btn.click()
    assert QGuiApplication.clipboard().text() == "heytora.co.za/r/DYLANH4821"
    panel._show("past")
    from PyQt6.QtWidgets import QLabel
    words = " ".join(l.text() for l in page.findChildren(QLabel))
    assert "Joined · 6 of 15 questions" in words


def test_the_foot_opens_invite_in_settings(shape, monkeypatch):
    monkeypatch.setattr(shape.bridge, "referral_status", lambda: {"code": "X", "link": "x"})
    shape.open_from("bottom", _pill_at("bottom"))
    foot_invite = next(b for b in shape.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QPushButton"]).QPushButton)
                       if b.text().startswith("Invite"))
    foot_invite.click()
    assert shape.mode == "settings" and shape.current_setting() == "invite"


def test_upgrade_is_gone_on_max_and_on_their_own_key(shape, monkeypatch):
    def state(plan, mode="account"):
        return lambda: {"signed_in": True, "mode": mode, "summary": {"plan": plan}}
    for plan, mode, shown in (("free", "account", True), ("pro", "account", True),
                              ("max", "account", False), ("free", "own_key", False)):
        monkeypatch.setattr(shape.bridge, "account_state", state(plan, mode))
        shape._upgrade_shown()
        assert (not shape.upgrade.isHidden()) == shown, (plan, mode)


def test_the_plans_are_three_cards_yours_outlined(shape, qapp, monkeypatch):
    from PyQt6.QtWidgets import QFrame
    monkeypatch.setattr(shape.bridge, "account_state",
                        lambda: {"signed_in": True, "mode": "account", "summary": {"plan": "pro"}})
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings("plans")
    cards = [f for f in shape.settings_stack.currentWidget().findChildren(QFrame) if f.objectName() == "planCard"]
    assert len(cards) == 3
    assert [c.property("mine") for c in cards] == ["false", "true", "false"]


def test_old_tab_names_land_on_their_new_pages(shape):
    """The app window's General was split, its Usage folded into Account."""
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings("general")
    assert shape.current_setting() == "shortcuts"
    shape.show_settings("usage")
    assert shape.current_setting() == "account"


def test_small_dialogs_sit_over_settings(shape):
    """Change shortcut, Sign out?, Delete account: settings_tabs puts them
    over `window()._settings` when it shows."""
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings("shortcuts")
    tab = shape.settings_stack.currentWidget()
    assert tab.window() is shape and shape._settings.isVisible()


def test_home_fits_the_shape(shape, qapp):
    from ui import shape as S
    shape.open_from("bottom", _pill_at("bottom"))
    qapp.processEvents()
    assert shape.body.minimumSizeHint().width() <= S.OPEN_W
    assert shape.stack.currentWidget().width() == S.OPEN_W


def test_the_greeting_and_the_status_line():
    from ui.shape_pages import greeting, status_line
    assert greeting("Dylan", 20) == "Evening, Dylan."
    assert greeting("", 9) == "Good morning."
    assert greeting("Sam", 14) == "Afternoon, Sam."
    assert status_line("idle", 1) == "1 job working"
    assert status_line("listening", 3) == "3 jobs working", "jobs first"
    assert status_line("thinking", 0) == "Thinking"
    assert status_line("idle", 0) == "Ready"


def test_the_card_is_the_newest_finished_thing():
    from ui.shape_pages import newest_result
    job = {"id": "t1", "title": "Tidy my downloads", "status": "completed", "when": 200.0}
    running = {"id": "t2", "title": "Still going", "status": "running", "when": 300.0}
    answer = {"question": "What is this error?", "answer": "A missing import.", "created_at": 100.0}
    assert newest_result([running, job], [answer])["title"] == "Tidy my downloads"
    later = dict(answer, created_at=250.0)
    assert newest_result([job], [later])["kind"] == "answer"
    assert newest_result([running], []) == {}, "a running job is not a result"
    failed = dict(job, status="failed", error="The folder was gone.")
    assert newest_result([failed], [])["text"] == "The folder was gone."


def test_it_takes_its_colours_from_the_tokens():
    from tests.agents.test_e2e_design_tokens import ROOT, _code_hexes
    for f in ("ui/shape.py", "ui/shape_pages.py"):
        assert not _code_hexes(ROOT / f), f


# ── no glitches (28 Oct 2026, heytoraPopup.mp4, frame by frame) ──────────

def test_the_window_never_moves_while_the_shape_does(shape):
    """Moved or resized mid-animation, Windows showed one frame of the old
    picture in the new place: the page jumped 30 px on close."""
    shape.open_from("top", _pill_at("top"))
    where = shape.geometry()
    shape.show_settings("account")
    shape.back()
    shape.show_settings("plans")
    assert shape.geometry() == where
    shape.shrink()
    assert shape.geometry() == where


def test_the_first_frame_is_the_pill(qapp, monkeypatch):
    """The clock started at show(); the first frame Windows drew was a third
    grown - a big black box popping out, not growing."""
    from ui import shape as S
    from ui.app_bridge import AppBridge
    monkeypatch.setattr(S, "animations_on", lambda: True)
    s = S.Shape(AppBridge())
    pill = _pill_at("bottom")
    s.open_from("bottom", pill)
    assert s._anim is None, "the clock waits for the window to be on the screen"
    first = s.shape_rect().translated(s.x(), s.y())
    assert abs(first.width() - pill.width()) < 1 and abs(first.height() - pill.height()) < 1
    assert not s.body.isVisible()
    s._start_open()
    assert s._anim is not None
    s._anim.stop()
    s.hide()


def test_a_change_of_size_never_shows_the_new_page_early_or_an_empty_box(shape):
    """Into Settings: the Settings page showed full size in the small shape
    for a frame, then the shape was an empty black box for 80 ms."""
    from ui import shape as S
    shape.open_from("bottom", _pill_at("bottom"))
    shape._run = lambda to, ms: None             # hold the move at its start
    shape.show_settings("account")
    assert not shape.body.isVisible(), "the new page waits"
    assert shape._ghost is not None, "the old page fades out meanwhile"
    shape._step(S.GHOST_PART)
    assert shape._ghost is None


def test_closing_fades_the_page_before_the_shape_shrinks(shape):
    from ui import shape as S
    shape.open_from("bottom", _pill_at("bottom"))
    shape._run = lambda to, ms: None
    shape.shrink()
    full = shape.shape_rect()
    shape._step(1.0 - S.CLOSE_FADE - 1e-9)
    assert not shape.body.isVisible(), "the page is gone ..."
    now = shape.shape_rect()
    assert abs(now.width() - full.width()) < 0.01 and abs(now.top() - full.top()) < 0.01,         "... before the shape moves"


# ── Ask: typed questions (29 Oct 2026) ─────────────────────────────────────

def test_ask_tucks_the_shape_away_then_asks(shape, monkeypatch):
    from PyQt6.QtCore import QTimer
    asked, later = [], []
    monkeypatch.setattr(shape.bridge, "ask", asked.append)
    import ui.shape as _S
    monkeypatch.setattr(_S, "later", lambda owner, fn, ms=0: later.append(fn))
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_tab("chat")
    chat = shape.stack.currentWidget()
    chat.ask_box.setText("  Why won't this total add up?  ")
    chat.ask_box.returnPressed.emit()
    assert not shape.is_open() and asked == [], "out of the picture before she looks"
    assert chat.ask_box.text() == ""
    for fn in later:
        fn()
    assert asked == ["Why won't this total add up?"]


def test_an_empty_ask_does_nothing(shape):
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_tab("chat")
    chat = shape.stack.currentWidget()
    chat.ask_box.setText("   ")
    chat.ask_box.returnPressed.emit()
    assert shape.is_open()


def test_a_question_half_typed_survives_the_history_redrawing(shape):
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_tab("chat")
    chat = shape.stack.currentWidget()
    chat.ask_box.setText("What does this cla")
    chat.refresh()                                   # an answer landed in the journal
    chat.refresh()
    assert chat.ask_box.text() == "What does this cla"
    assert chat.lay.count() == 2, "one history, one box - not a pile of them"


# ── Jobs, in the shape (29 Oct 2026) ───────────────────────────────────────

class _Agents:
    """The workforce, as the Jobs tab reads it through the bridge."""

    def __init__(self):
        self.approvals_ = [{"id": "a1", "reason": "Send the email to Sam", "task": "Reply to Sam",
                            "tool": "gmail_send", "sensitive": True, "confirm_word": "SEND"}]
        self.running_ = [{"id": "t1", "title": "Tidy my downloads", "doing": "Moving files",
                          "step": 2, "total": 5, "progress": 0.3, "status": "running"}]
        self.tasks_ = [{"id": "t0", "title": "Compare laptops", "status": "completed", "when": 0.0}]
        self.calls = []

    def install(self, bridge, monkeypatch):
        monkeypatch.setattr(bridge, "approvals", lambda: list(self.approvals_))
        monkeypatch.setattr(bridge, "running", lambda: [dict(j) for j in self.running_])
        monkeypatch.setattr(bridge, "tasks", lambda limit=8: list(self.tasks_))
        monkeypatch.setattr(bridge, "agents", lambda: [])
        for name in ("answer_approval", "pause_job", "resume_job", "stop_job"):
            monkeypatch.setattr(bridge, name, lambda *a, n=name: self.calls.append((n,) + a) or True)
        monkeypatch.setattr(bridge, "give_job", lambda text: self.calls.append(("give_job", text))
                            or (("chat", "") if text.endswith("?") else ("started", text)))


def _jobs(shape, monkeypatch):
    work = _Agents()
    work.install(shape.bridge, monkeypatch)
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_tab("agents")
    return shape.stack.currentWidget(), work


def test_jobs_is_a_tab_of_its_own_and_new_job_opens_it_ready(shape, monkeypatch):
    from ui.shape_pages import JobsTab
    work = _Agents()
    work.install(shape.bridge, monkeypatch)
    shape.open_from("bottom", _pill_at("bottom"))
    shape._new_job()
    assert isinstance(shape.stack.currentWidget(), JobsTab) and shape._tabs["agents"].isChecked()
    assert shape.opened == [], "no app window"


def test_a_job_is_started_and_a_question_is_sent_to_chat(shape, monkeypatch):
    page, work = _jobs(shape, monkeypatch)
    page.job_box.setText("Build a site for my coffee shop")
    page.job_box.returnPressed.emit()
    assert ("give_job", "Build a site for my coffee shop") in work.calls
    assert page.job_box.text() == "" and "Started" in page.note.text()
    page.job_box.setText("What is a pivot table?")
    page.job_box.returnPressed.emit()
    assert "Chat" in page.note.text() and page.job_box.text() == "What is a pivot table?"


def test_a_sensitive_approval_sends_its_typed_word(shape, monkeypatch):
    from PyQt6.QtWidgets import QLineEdit, QPushButton
    page, work = _jobs(shape, monkeypatch)
    card = page._parts["approvals"].itemAt(1).widget()
    word = card.findChild(QLineEdit)
    word.setText("SEND")
    approve = next(b for b in card.findChildren(QPushButton) if b.text() == "Approve")
    work.approvals_.clear()
    approve.click()
    assert ("answer_approval", "a1", True, "SEND") in work.calls


def test_progress_never_wipes_what_is_being_typed(shape, monkeypatch):
    """Running jobs report progress all the time."""
    from PyQt6.QtWidgets import QLineEdit
    page, work = _jobs(shape, monkeypatch)
    page.job_box.setText("Rename my ph")
    word = page._parts["approvals"].itemAt(1).widget().findChild(QLineEdit)
    word.setText("SE")
    work.running_[0]["progress"] = 0.8
    page.refresh()
    page.refresh()
    assert page.job_box.text() == "Rename my ph"
    assert page._parts["approvals"].itemAt(1).widget().findChild(QLineEdit) is word and word.text() == "SE"
    assert abs(page._meters["t1"]._f - 0.8) < 1e-6, "the progress moved, in place"


def test_pause_stop_and_history(shape, monkeypatch):
    from PyQt6.QtWidgets import QPushButton
    page, work = _jobs(shape, monkeypatch)
    card = page._parts["running"].itemAt(1).widget()
    buttons = {b.text(): b for b in card.findChildren(QPushButton)}
    buttons["Pause"].click()
    buttons["Stop"].click()
    assert ("pause_job", "t1") in work.calls and ("stop_job", "t1") in work.calls
    row = page._parts["history"].itemAt(1).widget()
    row._on_click()
    assert shape.opened == ["agents/job/t0"], "its details, over the Jobs tab"


def test_with_nothing_at_all_jobs_says_what_it_is_for(shape, monkeypatch):
    page, work = _jobs(shape, monkeypatch)
    work.approvals_.clear(), work.running_.clear(), work.tasks_.clear()
    page.refresh()
    assert page._parts["history"].count() == 1
    assert page._parts["approvals"].count() == 0 and page._parts["running"].count() == 0


def test_jobs_loads_before_the_app_window_ever_has():
    """agents_page and app_pages import each other: the Jobs tab, first in a
    fresh process, must not start the circle at the wrong end."""
    import subprocess
    import sys
    code = ("import os; os.environ['QT_QPA_PLATFORM']='offscreen';"
            "from PyQt6.QtWidgets import QApplication; app = QApplication([]);"
            "from ui.shape_pages import _status_words; print(_status_words()('running')[0])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(__import__('pathlib').Path(__file__).resolve().parents[2]))
    assert out.returncode == 0, out.stderr[-600:]
    assert out.stdout.strip() == "Working"


def test_account_shows_the_month_on_the_plan_when_signed_in(shape, qapp, monkeypatch):
    """HeyClicky's Account: the plan and its use first. Signed out, the
    usage part was never drawn - and a name it needed went unnoticed."""
    from PyQt6.QtWidgets import QLabel
    summary = {"plan": "pro", "plan_name": "Pro", "plan_status": "active",
               "limits": {"talk": 450, "agent": 100}, "usage": {"talk": 120, "agent": 12}}
    monkeypatch.setattr(shape.bridge, "account_state", lambda: {
        "signed_in": True, "mode": "account", "email": "you@example.com", "configured": True,
        "error": "", "busy": False, "fresh": True, "own_key": False, "website": "", "summary": summary})
    monkeypatch.setattr(shape.bridge, "refresh_account", lambda force=False: None)
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings("account")
    page = shape.settings_stack.currentWidget()
    words = " ".join(l.text() for l in page.findChildren(QLabel))
    assert "you@example.com" in words and "Questions" in words


def test_all_of_insights_fits_the_shape(shape, qapp):
    """Built for the old window's 1040 px column, the third tile and the
    streak ran off the shape's right edge (30 Oct): in the shape it is laid
    out narrow - and must stay narrower than it."""
    from ui import shape as S
    from ui import tokens
    tokens.load_fonts()
    shape.open_from("bottom", _pill_at("bottom"))
    shape.go("insights/all")
    qapp.processEvents()
    page = shape.page_stack.currentWidget()
    assert page.NARROW
    assert page.body.widget().minimumSizeHint().width() <= S.SETTINGS_W


def test_a_late_scroll_never_touches_a_page_that_is_gone(qapp):
    """30 Oct: QTimer.singleShot(0, lambda: bar.setValue(...)) ran after its
    page was deleted; the error inside Qt's loop made PyQt abort the run.
    later() ties the delay to the page."""
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QWidget
    from ui.app_widgets import later
    ran = []
    page = QWidget()
    later(page, lambda: ran.append(1))
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for _ in range(5):
        qapp.processEvents()
    assert ran == []
    alive = QWidget()
    later(alive, lambda: ran.append(2))
    for _ in range(5):
        qapp.processEvents()
    assert ran == [2]
