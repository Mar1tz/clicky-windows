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
    opened = []
    s = S.Shape(AppBridge(), open_app=opened.append)
    s.opened = opened
    yield s
    s.hide()
    s.deleteLater()


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

def test_home_is_its_own_and_the_rest_opens_the_app_window(shape):
    from ui.shape_pages import HomeTab
    assert isinstance(shape.stack.currentWidget(), HomeTab)
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_tab("insights")
    assert shape.opened == ["insights"]
    assert isinstance(shape.stack.currentWidget(), HomeTab), "the tab stays on Home"
    assert shape._tabs["home"].isChecked()
    assert not shape.is_open(), "it steps aside for the app window"


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


def test_invite_and_earn_opens_the_referral_window(shape):
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings()
    shape.select("invite")
    assert shape.opened == ["invite"] and not shape.is_open()


def test_small_dialogs_sit_over_settings(shape):
    """Change shortcut, Sign out?, Delete account: settings_tabs puts them
    over `window()._settings` when it shows."""
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings("general")
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
