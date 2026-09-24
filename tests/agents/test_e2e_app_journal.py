"""APP-15: the Journal page is live.

It was a drawing (M29): three made-up questions "due for review", three
made-up entries, and buttons that did nothing. Now it reads the journal
Tora keeps, reviews it honestly - you see the answer, then say whether you
knew it, and that decides when it comes back (the voice "quiz me" marks
every answer right) - and recording, capture, the folder and attach all go
through the tray's own handlers.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, pyqtSignal               # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from ui.app_bridge import AppBridge                        # noqa: E402

DAY = 86400


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """The journal module, writing to this test's own database."""
    from tutor_features import journal as j
    monkeypatch.setattr(j, "_db_path", lambda: tmp_path / "journal.db")
    return j


class FakeManager(QObject):
    sig_state_changed = pyqtSignal(object)
    sig_agent_event = pyqtSignal(object)
    sig_recording_state = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self.agents = None
        self.is_recording = False
        self._workflow = None


class FakeTray(QObject):
    on_journal_open = pyqtSignal()
    on_attach_doc = pyqtSignal()
    on_record_start = pyqtSignal()
    on_record_stop = pyqtSignal()
    on_workflow_start = pyqtSignal()
    on_workflow_stop = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.done = []
        for name in ("on_journal_open", "on_attach_doc", "on_record_start",
                     "on_record_stop", "on_workflow_start", "on_workflow_stop"):
            getattr(self, name).connect(lambda _n=name: self.done.append(_n))

    def apply_switch(self, name, on):
        self.done.append(("switch", name, on))


def _live():
    mgr, tray = FakeManager(), FakeTray()
    return mgr, tray, AppBridge(mgr, tray)


def _due(journal, question, answer, *, days_old=3, streak=0, due_in=-3600.0):
    entry = journal.log_qa(question, answer)
    with journal._connect() as conn:
        conn.execute("UPDATE entries SET created_at = ?, next_review_at = ?, streak = ? "
                     "WHERE id = ?", (time.time() - days_old * DAY, time.time() + due_in,
                                      streak, entry))
    return entry


def _row(entry, journal):
    with journal._connect() as conn:
        return conn.execute("SELECT streak, next_review_at FROM entries WHERE id = ?",
                            (entry,)).fetchone()


def _texts(w):
    return [lbl.text() for lbl in w.findChildren(QLabel) if lbl.isVisibleTo(w)]


def _buttons(w, text):
    return [b for b in w.findChildren(QPushButton) if b.text() == text and b.isVisibleTo(w)]


def _settle(qapp):
    for _ in range(3):
        qapp.processEvents()


def _card(page, heading):
    """The card on the page with this title."""
    from ui.app_widgets import Card
    return next(c for c in page.findChildren(Card)
                if any(lbl.objectName() == "cardTitle" and lbl.text() == heading
                       for lbl in c.findChildren(QLabel)))


# ── reviewing ────────────────────────────────────────────────────────────────

def test_what_is_due_comes_from_the_journal(qapp, journal):
    from ui.app_pages import JournalPage
    _due(journal, "How do you share one window in Meet?", "Present now, then A window.")
    _due(journal, "Where is display scaling?", "Settings, System, Display.", streak=2)
    _due(journal, "What is a zero-rated supply?", "Taxable at 0%.", due_in=2 * DAY)

    _mgr, _tray, bridge = _live()
    texts = _texts(JournalPage(bridge))
    assert "How do you share one window in Meet?" in texts
    assert "asked yesterday · remembered twice" not in texts   # three days old
    assert "asked 3 days ago · remembered twice" in texts
    assert any(t.startswith("2 due now.") for t in texts)
    assert "How do you share a single window in Google Meet?" not in texts, "no made-up entries"


def test_seeing_the_answer_then_i_knew_it_sends_it_away(qapp, journal):
    from ui.app_pages import JournalPage
    entry = _due(journal, "How do you share one window in Meet?", "Present now, then A window.")
    _mgr, _tray, bridge = _live()
    page = JournalPage(bridge)

    assert "Present now, then A window." not in _texts(page), "hidden until asked for"
    _buttons(page, "Show answer")[0].click()
    _settle(qapp)
    assert "Present now, then A window." in _texts(page)

    before = time.time()
    _buttons(page, "I knew it")[0].click()
    _settle(qapp)
    streak, next_review = _row(entry, journal)
    assert streak == 1 and next_review >= before + 3 * DAY - 5, "known: back in three days"
    assert "How do you share one window in Meet?" not in _texts(_card(page, "Due for review"))
    assert "How do you share one window in Meet?" in _texts(_card(page, "Recent"))
    assert bridge.due() == []


def test_not_yet_brings_it_back_tomorrow(qapp, journal):
    from ui.app_pages import JournalPage
    entry = _due(journal, "Where is display scaling?", "Settings, System, Display.", streak=3)
    _mgr, _tray, bridge = _live()
    page = JournalPage(bridge)
    _buttons(page, "Show answer")[0].click()
    _settle(qapp)
    before = time.time()
    _buttons(page, "Not yet")[0].click()
    _settle(qapp)
    streak, next_review = _row(entry, journal)
    assert streak == 0
    assert before + DAY - 5 <= next_review <= before + DAY + 5


def test_a_review_does_not_give_the_answer_away_under_recent(qapp, journal):
    from ui.app_pages import JournalPage
    _due(journal, "How do you share one window in Meet?", "Present now, then A window.")
    _mgr, _tray, bridge = _live()
    page = JournalPage(bridge)
    assert len(_buttons(page, "Show")) == 1, "the same entry is listed under Recent too"
    _buttons(page, "Show answer")[0].click()
    _settle(qapp)
    assert _texts(page).count("Present now, then A window.") == 1
    assert len(_buttons(page, "Show")) == 1, "…and stays closed there"


def test_nothing_due_says_when_the_next_one_comes(qapp, journal):
    from datetime import date, datetime, timedelta
    from ui.app_pages import JournalPage
    noon_tomorrow = datetime.combine(date.today() + timedelta(days=1),
                                     datetime.min.time()).timestamp() + 12 * 3600
    _due(journal, "What is a zero-rated supply?", "Taxable at 0%.",
         due_in=noon_tomorrow - time.time())
    _mgr, _tray, bridge = _live()
    assert "Nothing due. The next one comes back tomorrow." in _texts(JournalPage(bridge))


def test_walkthroughs_are_listed_with_their_steps(qapp, journal):
    from ui.app_pages import JournalPage
    journal.log_walkthrough("export the video as MP4",
                            ["Click Export", "Pick MP4", "Click Export again"])
    _mgr, _tray, bridge = _live()
    page = JournalPage(bridge)
    texts = _texts(page)
    assert "How do I export the video as MP4?" in texts
    assert any(t.endswith("· walkthrough · 3 steps") for t in texts)
    _buttons(page, "Show")[0].click()
    _settle(qapp)
    assert "1. Click Export\n2. Pick MP4\n3. Click Export again" in _texts(page)


def test_saving_off_is_said_where_the_entries_are(qapp, journal, monkeypatch):
    from ui.app_pages import JournalPage
    _mgr, _tray, bridge = _live()
    monkeypatch.setattr(bridge, "switch", lambda name: name != "journal")
    assert "Saving is off - new questions are not being kept." in _texts(JournalPage(bridge))


# ── the tray's own handlers ──────────────────────────────────────────────────

@pytest.mark.parametrize("text, signal", [("Open folder", "on_journal_open"),
                                          ("Attach document", "on_attach_doc")])
def test_folder_and_attach_are_the_trays(qapp, journal, text, signal):
    from ui.app_pages import JournalPage
    _mgr, tray, bridge = _live()
    _buttons(JournalPage(bridge), text)[0].click()
    assert tray.done == [signal]


def test_recording_starts_and_stops_through_the_tray(qapp, journal):
    from ui.app_pages import JournalPage
    mgr, tray, bridge = _live()
    page = JournalPage(bridge)
    _buttons(page, "Start recording")[0].click()
    assert tray.done == ["on_record_start"]

    mgr.is_recording = True                  # what the manager does, then says so
    topics = []
    bridge.changed.connect(topics.append)
    mgr.sig_recording_state.emit(True, "C:/lessons/1")
    assert "recording" in topics and "recording" in page.TOPICS
    page.refresh()
    assert "Recording" in _texts(page)
    _buttons(page, "Stop recording")[0].click()
    assert tray.done == ["on_record_start", "on_record_stop"]


def test_capture_starts_and_stops_through_the_tray(qapp, journal):
    from ui.app_pages import JournalPage
    mgr, tray, bridge = _live()
    page = JournalPage(bridge)
    _buttons(page, "Capture my clicks")[0].click()
    assert tray.done == ["on_workflow_start"]

    mgr._workflow = SimpleNamespace(is_running=True)
    page.refresh()
    assert "Capturing" in _texts(page)
    _buttons(page, "Stop and send to Tora")[0].click()
    assert tray.done == ["on_workflow_start", "on_workflow_stop"]


def test_the_journal_switch_is_the_trays(qapp, journal):
    from ui.app_pages import JournalPage
    from ui.app_widgets import ToggleRow
    _mgr, tray, bridge = _live()
    page = JournalPage(bridge)
    (row,) = [r for r in page.findChildren(ToggleRow)]
    assert row.switch.isChecked(), "on by default"
    row.switch.setChecked(False)
    assert tray.done == [("switch", "journal", False)]


# ── the bridge ───────────────────────────────────────────────────────────────

def test_a_window_on_its_own_never_reads_the_journal(qapp, journal, monkeypatch):
    """No running app, no user: the real journal is never opened."""
    def _refuse():
        raise AssertionError("the journal was opened")
    monkeypatch.setattr(journal, "_connect", _refuse)
    bridge = AppBridge()
    assert bridge.due() == [] and bridge.recent() == [] and bridge.due_count() == 0
    assert bridge.next_due() is None
    bridge.review(1, True)                    # and grading is a no-op


def test_a_finished_turn_redraws_the_journal(qapp, journal):
    mgr, _tray, bridge = _live()
    topics = []
    bridge.changed.connect(topics.append)
    for state in ("LISTENING", "THINKING", "SPEAKING", "IDLE"):
        mgr.sig_state_changed.emit(SimpleNamespace(name=state))
    assert topics.count("journal") == 1, "once, when the turn ends"
    mgr.sig_state_changed.emit(SimpleNamespace(name="IDLE"))
    assert topics.count("journal") == 1, "idle to idle is not a turn"


def test_the_journal_counts_what_is_due(journal):
    _due(journal, "a?", "a.")
    _due(journal, "b?", "b.")
    _due(journal, "c?", "c.", due_in=3 * DAY)
    assert journal.due_count() == 2
    assert journal.next_due_at() == pytest.approx(time.time() + 3 * DAY, abs=5)
    assert [e["question"] for e in journal.recent(2)] == ["c?", "b?"]
