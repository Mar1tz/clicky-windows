"""Home's Up next card and the listening pop (DESIGN.md §3C).

The space at the top of Home holds the one useful thing: what is running,
else what is due for review, else three things to try for the work they
said they do. And when she starts listening, a pop they chose.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class _Bridge:
    """Just what Home reads."""

    def __init__(self, *, due=(), uses=(), agents=()):
        from PyQt6.QtCore import QObject, pyqtSignal

        class _Sig(QObject):
            changed = pyqtSignal(str)
        self._sig = _Sig()
        self.changed = self._sig.changed
        self._due, self._uses, self._agents = list(due), list(uses), list(agents)
        self.graded: list[tuple[int, bool]] = []

    def hotkey(self): return "Ctrl + Win"
    def problem(self): return ""
    def live(self): return True
    def walkthrough(self): return None
    def agents(self): return self._agents
    def due(self, limit=5): return self._due[:limit]
    def due_count(self): return len(self._due)
    def uses(self): return self._uses
    def recent(self, limit=8): return []
    def stats(self): return {"questions": 3, "walkthroughs": 1, "streak": 2}
    def account_state(self): return {}
    def show_panel(self): pass

    def review(self, entry_id, knew):
        self.graded.append((entry_id, knew))
        self._due = [e for e in self._due if e["id"] != entry_id]


def _texts(w):
    return [lbl.text() for lbl in w.findChildren(QLabel)]


def _click(w, text):
    next(b for b in w.findChildren(QPushButton) if b.text() == text).click()
    QApplication.processEvents()
    time.sleep(0.01)
    QApplication.processEvents()


def test_nothing_due_offers_things_to_try_for_their_work(qapp):
    from ui.app_pages import HomePage
    texts = _texts(HomePage(_Bridge(uses=["Accounting"])))
    assert "TRY ASKING" in texts
    assert "“Why won't this total add up?”" in texts


def test_three_suggestions_taken_in_turn_from_each_thing_they_do():
    from ui.app_pages import TRY_ANYWAY, try_asking
    assert try_asking(["Accounting", "Student"]) == [
        "Why won't this total add up?", "What's wrong with this equation?",
        "What does this line on the statement mean?"]
    assert try_asking([]) == list(TRY_ANYWAY), "nothing said: the general three"


def test_a_due_review_is_asked_shown_then_graded(qapp):
    from ui.app_pages import HomePage
    entry = {"id": 7, "question": "What is wrong with this equation?",
             "answer": "Subtract the 6.", "created_at": time.time() - 86400}
    bridge = _Bridge(due=[entry])
    page = HomePage(bridge)
    page.show()
    assert "What is wrong with this equation?" in _texts(page)
    assert "Subtract the 6." not in _texts(page), "the answer waits to be asked for"
    _click(page, "Show answer")
    assert "Subtract the 6." in _texts(page)
    _click(page, "I knew it")
    assert bridge.graded == [(7, True)]
    assert "TRY ASKING" in _texts(page), "nothing else due: back to suggestions"
    page.hide()


def test_running_work_comes_first(qapp):
    from ui.app_pages import HomePage
    agent = {"title": "Florist website", "doing": "Writing", "progress": 0.5, "step": 1, "total": 2}
    texts = _texts(HomePage(_Bridge(agents=[agent], due=[{"id": 1, "question": "q", "answer": "a",
                                                         "created_at": time.time()}])))
    assert "HAPPENING NOW" in texts and "Florist website" in texts and "50%" in texts
    assert "q" not in texts


def test_the_listening_pop_can_be_off_and_never_raises(qapp, monkeypatch):
    from ui import sounds
    assert sounds.play("off") is False and sounds.play("") is False
    assert sounds.play("no-such-sound") is False
    for key, _name in sounds.SOUNDS:
        from ui import tokens
        assert (tokens.assets_dir() / "sounds" / f"{key}.wav").exists(), key


def test_the_sound_choice_is_saved(qapp):
    from config import cfg
    cfg.set_listen_sound("chime")
    assert cfg.listen_sound() == "chime"
    cfg.set_listen_sound("off")
    assert cfg.listen_sound() == "off"


# ── two columns, search, no limit ────────────────────────────────────────────

class _History(_Bridge):
    def __init__(self, n):
        super().__init__()
        now = time.time()
        self.rows = [{"id": i, "question": f"question {i}" + (" equation" if i % 10 == 0 else ""),
                      "answer": "a", "created_at": now - i * 60, "app_key": "Excel", "tags": ""}
                     for i in range(n)]
        self.asked: list[int] = []

    def recent(self, limit=8):
        self.asked.append(limit)
        return self.rows[:limit]

    def search(self, text, limit=100):
        return [r for r in self.rows if text in r["question"]][:limit]


def _questions(page):
    return [t for t in _texts(page) if t.startswith("question ")]


def test_the_history_has_no_cap_it_loads_a_page_at_a_time(qapp):
    from ui.app_pages import HomePage
    bridge = _History(250)
    page = HomePage(bridge)
    assert len(_questions(page)) == HomePage.PAGE
    page._near_end(page.body.verticalScrollBar().maximum())
    QApplication.processEvents()
    time.sleep(0.01)
    QApplication.processEvents()
    assert len(_questions(page)) == 2 * HomePage.PAGE, "scrolled to the end: the next page"


def test_search_finds_and_clearing_brings_everything_back(qapp):
    from ui.app_pages import HomePage
    page = HomePage(_History(40))
    page._find("equation")
    assert _questions(page) == [f"question {i} equation" for i in (0, 10, 20, 30)]
    page._find("nothing like this")
    assert "Nothing asked mentions “nothing like this”" in _texts(page)
    page._find("")
    assert len(_questions(page)) == 40


def test_the_numbers_stay_put_while_the_history_scrolls(qapp):
    """Wispr's usage card: sticky in its own column."""
    from ui.app_pages import HomePage
    page = HomePage(_History(120))
    page.resize(1200, 700)
    page.show()
    for _ in range(5):
        QApplication.processEvents()
    aside = page.body._aside
    assert aside is not None and aside.parent() is page.body.viewport(), "on the viewport, not the content"
    bar = page.body.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    for _ in range(3):
        QApplication.processEvents()
    assert aside.y() == page.body.STICKY, "scrolled: held at the top"
    page.hide()


# ── a history row ─────────────────────────────────────────────────────────────

def _row(qapp, **entry):
    from ui.app_parts import history_row
    e = {"id": 1, "question": "Why won't this total add up? " * 3,
         "answer": "Car hire is typed as text, so the sum skips it. " * 4,
         "created_at": time.time(), "app_key": "Excel", "tags": ""} | entry
    row = history_row(e, False, lambda: None)
    row.resize(640, row.sizeHint().height())
    row.show()
    QApplication.processEvents()
    return row


def test_hovering_a_row_does_not_reflow_its_text(qapp):
    """Wispr's actions have a column of their own; showing them must not
    squeeze the words onto another line."""
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QEnterEvent
    row = _row(qapp)
    QApplication.sendEvent(row, QEvent(QEvent.Type.Leave))   # offscreen: the pointer starts on it
    QApplication.processEvents()
    words = [lbl for lbl in row.findChildren(QLabel) if lbl.objectName() in ("rowTitle", "rowSub")]
    before = [lbl.geometry() for lbl in words]
    assert not any(b.isVisible() for b in row.actions.findChildren(QPushButton))
    QApplication.sendEvent(row, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    QApplication.processEvents()
    assert any(b.isVisible() for b in row.actions.findChildren(QPushButton)), "hover shows copy"
    assert [lbl.geometry() for lbl in words] == before
    QApplication.sendEvent(row, QEvent(QEvent.Type.Leave))
    row.hide()


def test_a_row_names_the_app_only_when_it_was_another_app(qapp):
    assert "in Excel" in _texts(_row(qapp))
    assert not any(t.startswith("in ") for t in _texts(_row(qapp, app_key="AI Companion")))
