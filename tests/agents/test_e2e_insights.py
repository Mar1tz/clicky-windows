"""The Insights page counts; it never estimates (ui/insights_data.py)."""
from __future__ import annotations

import os
from collections import Counter
from datetime import date, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from ui import insights_data as data

TODAY = date(2026, 10, 4)                      # a Sunday
NOW = datetime(2026, 10, 4, 12).timestamp()


def _at(days_ago: int, hour: int = 10) -> float:
    return datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time()).timestamp() + hour * 3600


def test_questions_walkthroughs_and_the_week_on_week():
    rows = [(_at(0), "", "Excel", 0), (_at(1), "", "Excel", 1), (_at(2), "walkthrough", "Chrome", 0),
            (_at(9), "", "Word", 2)]
    d = data.summarise(rows, walkthrough_seconds=150, today=TODAY, now=NOW)
    assert (d["questions"], d["walkthroughs"], d["guided_minutes"]) == (3, 1, 2)
    assert (d["this_week"], d["last_week"], d["change"]) == (2, 1, 100)
    assert d["remembered"] == 2 and d["learnable"] == 4


def test_where_you_ask_leaves_tora_out():
    rows = [(_at(0), "", "Excel", 0)] * 3 + [(_at(0), "", "AI Companion", 0)] * 5 + [(_at(0), "", "", 0)]
    d = data.summarise(rows, today=TODAY, now=NOW)
    assert d["apps"] == [("Excel", 3, 1.0)] and d["apps_used"] == 1


def test_streaks_today_or_yesterday_and_the_longest():
    days = [0, 1, 2, 10, 11, 12, 13]
    d = data.summarise([(_at(n), "", "", 0) for n in days], today=TODAY, now=NOW)
    assert (d["streak"], d["longest"]) == (3, 4)
    d = data.summarise([(_at(n), "", "", 0) for n in (1, 2)], today=TODAY, now=NOW)
    assert d["streak"] == 2, "today not over: yesterday's streak still stands"


def test_the_calendar_is_weeks_of_days_ending_today():
    cols = data.calendar(Counter({TODAY: 2}), today=TODAY, weeks=3)
    assert len(cols) == 3 and [len(c) for c in cols] == [7, 7, 1]
    assert cols[0][0][0].weekday() == 6, "a column starts on Sunday"
    assert cols[-1][-1] == (TODAY, 2)


def test_the_plan_and_this_months_agents():
    assert data.plan({"limits": {"talk": 25, "agent": None}, "usage": {"talk": 24}}) == [
        ("Talk messages", 24, 25), ("Agent messages", 0, None)]
    assert data.plan(None) == []
    first = datetime(2026, 10, 1).timestamp()
    a = data.agents_month([{"status": "completed", "when": first + 10, "title": "a"},
                           {"status": "failed", "when": first + 20, "title": "b"},
                           {"status": "running", "when": first + 30, "title": "c"},
                           {"status": "completed", "when": first - 86400, "title": "september"}],
                          today=TODAY)
    assert (a["done"], a["failed"], a["running"]) == (1, 1, 1)


def test_the_page_builds_with_nothing_measured():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication, QLabel
    app = QApplication.instance() or QApplication([])   # kept: an unreferenced app is deleted at once
    from ui.app_pages import InsightsPage

    class Bare:
        def __init__(self):
            from PyQt6.QtCore import QObject, pyqtSignal

            class S(QObject):
                changed = pyqtSignal(str)
            self._s = S()
            self.changed = self._s.changed

    bridge = Bare()
    page = InsightsPage(bridge)
    texts = [l.text() for l in page.findChildren(QLabel)]
    assert "QUESTIONS ASKED" in texts and "0 day streak" in texts
    assert not any("%" in t and "top" in t.lower() for t in texts), "no percentile claims"
    assert app is not None


def test_the_charts_grow_in_when_the_page_opens_not_on_a_redraw():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # kept: an unreferenced app is deleted at once
    from ui.app_parts import DayBars, Gauge, Grows
    from ui.app_pages import InsightsPage

    class Bridge:
        def __init__(self):
            from PyQt6.QtCore import QObject, pyqtSignal

            class S(QObject):
                changed = pyqtSignal(str)
            self._s = S()
            self.changed = self._s.changed

        def insights(self):
            d = data.summarise([(_at(0), "", "Excel", 1), (_at(1), "", "Excel", 0)], today=TODAY, now=NOW)
            d.update(agents=data.agents_month([]), plan=[], plan_summary=None, due=0)
            return d

    bridge = Bridge()
    page = InsightsPage(bridge)
    charts = page.findChildren(Grows)
    assert any(isinstance(c, DayBars) for c in charts) and any(isinstance(c, Gauge) for c in charts)
    assert all(c.t == 1.0 for c in charts), "built drawn: a live redraw does not replay"
    page.show()
    app.processEvents()
    assert all(c.t < 1.0 for c in page.findChildren(Grows)), "opened: they start from nothing"
    # Waited on Qt's own clock (other tests stand in for time.time and
    # time.sleep), long enough for every chart's start - staggered - to fire.
    # The tween itself is checked by where it is going, not by waiting for
    # it: after other UI tests Qt's animation clock can sit still offscreen.
    from PyQt6.QtCore import QEventLoop, QTimer
    wait = QEventLoop()
    QTimer.singleShot(600, wait.quit)
    wait.exec()
    for c in page.findChildren(Grows):
        tween = c.__dict__.get("_tweens", {}).get("t")
        assert c.t == 1.0 or (tween is not None and tween.endValue() == 1.0), type(c).__name__
    page.hide()


def test_a_day_series_ends_today():
    assert data.daily(Counter({TODAY: 3, TODAY - timedelta(days=2): 1}), days=4, today=TODAY) == [0, 1, 0, 3]
