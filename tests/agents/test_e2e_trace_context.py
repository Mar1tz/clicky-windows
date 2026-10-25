"""The held-key trace as context (PT-05, PT-06, the Massive Direction Plan).

A trace tells Tora where to look - never that she may click, type or change
what is there. And when another window is in front by the release, what was
read under the trace may no longer be what is on screen: she is told, and
asks or looks again instead of trusting it.
"""
from __future__ import annotations

import companion_manager as cm
from ai import trace_read
from ai.trace import Focus


def _reading():
    return trace_read.Reading(Focus("circle", (100, 100, 300, 160), 0.2, 0.9))


def test_a_trace_is_attention_not_consent():
    line = trace_read.voice_line([_reading()])
    assert trace_read.NOT_CONSENT in line and "not permission to click" in line
    assert "window changed" not in line


def test_another_window_by_the_release_is_said(monkeypatch):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    windows = iter([(11, "Quarterly report - Word"), (22, "Inbox - Gmail - Opera")])
    monkeypatch.setattr(cm, "_window_in_front", lambda: next(windows))
    mgr._trace_window = cm._window_in_front()
    changed = mgr._window_changed_since_trace()
    assert changed == 'from "Quarterly report - Word" to "Inbox - Gmail - Opera"'
    line = trace_read.voice_line([_reading()], changed=changed)
    assert "The window changed while they traced" in line and "ask or look again" in line


def test_the_same_window_says_nothing(monkeypatch):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    monkeypatch.setattr(cm, "_window_in_front", lambda: (11, "Quarterly report - Word"))
    mgr._trace_window = cm._window_in_front()
    assert mgr._window_changed_since_trace() == ""
