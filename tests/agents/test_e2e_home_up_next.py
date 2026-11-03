"""The listening pop, and the rows of everything asked (DESIGN.md §3C).

When she starts listening, a pop they chose. (The old Home's Up next card
these tests began with went with the app window, revamp step 6b; the
shape's Home has its own - tests/agents/test_e2e_shape.py.)
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


def _texts(w):
    return [lbl.text() for lbl in w.findChildren(QLabel)]


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
