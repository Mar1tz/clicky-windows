"""Settings, closer to HeyClicky (31 Oct 2026, the owner's walkthrough).

"Ours has a lot of fluff": Account is the plan card and three rows - no
name box, no picture, no "how Tora thinks". General: a taskbar button and
launch at login (the floating panel's switch flipped itself off and went),
and the welcome tour again. Type to Tora is not a switch. Agents is two
short groups, the limits one step in. The shape makes a soft sound as it
opens and grows into Settings, which can be turned off.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")

from tests.agents.test_e2e_shape import _pill_at, qapp, shape  # noqa: E402,F401 - fixtures

SUMMARY = {"plan": "free", "plan_name": "Free", "plan_status": "active",
           "limits": {"talk": 25, "agent": 25}, "usage": {"talk": 3, "agent": 0},
           "period_end": "2026-11-28T00:00:00+00:00"}


def _settled_then_animating(shape, monkeypatch) -> None:
    """Open at once (the fixture has animations off), then animations on for
    what is under test. Real time is not used: after a long run Qt's shared
    animation clock has been seen stuck at 0 in the test process, with plain
    timers still firing, so these step each slide by hand (_finish)."""
    from ui import shape as S
    shape.open_from("bottom", _pill_at("bottom"))
    assert shape.mode == "open" and shape._t >= 1.0
    monkeypatch.setattr(S, "animations_on", lambda: True)
    monkeypatch.setattr(type(shape), "_left", lambda self: None)   # a stray window's focus: not under test


def _finish(page) -> None:
    anim = getattr(page, "_slide", None)
    if anim is not None:
        anim.setCurrentTime(anim.duration())


def _page(shape, monkeypatch, key):
    from PyQt6.QtWidgets import QLabel
    monkeypatch.setattr(shape.bridge, "account_state", lambda: {
        "signed_in": True, "mode": "account", "email": "you@example.com", "configured": True,
        "error": "", "busy": False, "fresh": True, "own_key": False, "website": "", "summary": SUMMARY})
    monkeypatch.setattr(shape.bridge, "refresh_account", lambda force=False: None)
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings(key)
    page = shape.settings_stack.currentWidget()
    return page, [lbl.text() for lbl in page.findChildren(QLabel)]


def test_account_is_the_plan_and_three_rows(shape, monkeypatch):
    from PyQt6.QtWidgets import QLineEdit, QPushButton
    page, words = _page(shape, monkeypatch, "account")
    assert "You're on the Free plan." in words and "3 / 25" in words
    assert {"Log out", "Delete account", "Quit HeyTora", "you@example.com"} <= set(words)
    assert not page.findChildren(QLineEdit), "no name box"
    assert not {"Name", "Picture", "Profile", "How Tora thinks"} & set(words)
    went = []
    page.navigate.connect(went.append)
    next(b for b in page.findChildren(QPushButton) if b.text() == "Upgrade").click()
    assert went == ["settings/plans"]


def test_general_has_the_taskbar_and_the_tour_not_the_floating_panel(shape, monkeypatch):
    _page_, words = _page(shape, monkeypatch, "system")
    assert {"Show in taskbar", "Launch at login", "Replay the welcome tour"} <= set(words)
    assert "Show the floating panel" not in words


def test_type_to_tora_has_no_switch(shape, monkeypatch):
    from ui.app_widgets import Switch
    page, words = _page(shape, monkeypatch, "shortcuts")
    assert "Type to Tora" in words and not page.findChildren(Switch)


def test_agents_is_short_and_the_limits_are_one_step_in(shape, monkeypatch):
    page, words = _page(shape, monkeypatch, "agents")
    assert {"Agent folder", "Approve automatically", "Show me the plan first", "Limits & models"} <= set(words)
    assert not {"Jobs at once", "Time limit", "Token budget"} & set(words)
    went = []
    page.navigate.connect(went.append)
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from ui.settings_parts import SettingRow
    row = next(r for r in page.findChildren(SettingRow) if r.title.text() == "Limits & models")
    click = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(5, 5), QPointF(5, 5),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    row.mouseReleaseEvent(click)
    assert went == ["settings/agent-limits"]
    _limits, more = _page(shape, monkeypatch, "agent-limits")
    assert {"Jobs at once", "Time limit", "Token budget", "Run without asking, up to"} <= set(more)
    assert "Approve automatically" not in more, "not twice"


def test_interface_sounds_play_as_it_opens_and_can_be_turned_off(shape, monkeypatch):
    from config import cfg
    from ui import sounds, tokens
    played = []
    monkeypatch.setattr(sounds, "_effect", lambda key, volume=sounds.VOLUME:
                        type("Fx", (), {"stop": lambda self: None,
                                        "play": lambda self: played.append(key)})())
    monkeypatch.setenv("TORA_UI_SOUNDS", "1")
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings("voice")
    shape.show_settings("cursor")                      # another page in Settings: the tick, not the chime again
    shape.show_tab("home")                             # back down to the tabs
    shape._t = 1.0                                     # (no animation here: settled at once)
    shape.show_tab("chat")                             # a tab inside the open shape
    shape.shrink()                                     # into the pill
    assert played == [sounds.UI[k] for k in ("open", "settings", "tab", "back", "tab", "close")]
    assert not set(sounds.UI.values()) & {k for k, _n in sounds.SOUNDS},         "its own sounds, not the listening pops"
    assert all((tokens.assets_dir() / "sounds" / f"{sounds.UI[k]}.wav").exists() for k in sounds.UI)
    monkeypatch.setenv("TORA_UI_SOUNDS", "0")
    assert not cfg.switch("ui_sounds") and sounds.ui("open") is False


def test_the_taskbar_button_follows_its_switch_and_opens_the_shape(qapp, monkeypatch):
    from ui import taskbar
    monkeypatch.setenv("TORA_TASKBAR", "1")
    opened = []
    b = taskbar.install(lambda: opened.append(1))
    try:
        assert b.isVisible() and b.isMinimized()
        b.clicked.emit()
        assert opened == [1]
        taskbar.apply(False)
        assert not b.isVisible()
        taskbar.apply(True)
        assert b.isVisible()
    finally:
        taskbar.apply(False)
        taskbar._button.clear()
        b.deleteLater()


def test_main_puts_heytora_in_the_taskbar():
    from pathlib import Path
    main = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    assert "taskbar.install(_toggle_shape)" in main


def test_account_shows_when_they_used_heytora_and_links_all_of_insights(shape, monkeypatch):
    """31 Oct: "the old Insights had cool graphs - the GitHub-style map of
    when they've used HeyTora - put that in Account"."""
    from PyQt6.QtWidgets import QPushButton
    from ui.app_parts import StreakGrid
    page, words = _page(shape, monkeypatch, "account")
    assert page.findChildren(StreakGrid) and any(w.endswith("day streak") for w in words)
    went = []
    page.navigate.connect(went.append)
    next(b for b in page.findChildren(QPushButton) if b.text() == "All insights").click()
    assert went == ["insights/all"]


def test_the_tours_last_step_has_one_way_on(qapp, monkeypatch):
    """31 Oct: "why does it have an extra button?" - the plan they are on
    loses its "Your plan" button and Skip goes; Start using HeyTora stays."""
    from types import SimpleNamespace
    from PyQt6.QtWidgets import QPushButton
    import ui.onboarding_shape as O
    import ui.onboarding_steps as St
    from tests.agents.test_e2e_onboarding_steps import throw_away
    from ui.app_bridge import AppBridge
    monkeypatch.setattr(O, "animations_on", lambda: False)
    br = AppBridge()
    br.account_state = lambda: {"signed_in": True, "mode": "account", "configured": True,
                                "summary": {"plan": "free"}}
    p = O.OnboardingPanel([St.HelloStep, St.PlansStep])
    p.live = SimpleNamespace(bridge=br)
    try:
        from PyQt6.QtCore import QRectF
        p.open_from(QRectF(900, 1000, 46, 10))
        assert p.skip_button.isVisibleTo(p)
        p.next()
        shown = [b.text() for b in p.findChildren(QPushButton) if b.isVisibleTo(p)]
        assert "Your plan" not in shown and "Skip" not in shown
        assert "Start using HeyTora" in shown and "Choose Pro" in shown
    finally:
        throw_away(p)


def test_ctrl_ctrl_opens_chat_at_the_newest_message(shape, qapp, monkeypatch):
    """31 Oct: "when we open the chat via Ctrl, Ctrl it should be at the
    bottom, not have to scroll down"."""
    import time
    rows = [{"question": f"Question number {i}, asked in full so it wraps a little", "answer":
             f"Answer {i}: " + "words " * 30} for i in range(30)]
    monkeypatch.setattr(shape.bridge, "recent", lambda limit=30: rows[:limit])
    shape.open_to_type("bottom", _pill_at("bottom"))
    end = time.monotonic() + 0.6
    while time.monotonic() < end:
        qapp.processEvents()
    bar = shape._pages["chat"]._area.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == bar.maximum()


def test_the_plan_is_measured_in_credits(shape, monkeypatch):
    """31 Oct: "we measure via token spend, not those three things" - one
    credits meter, not questions, jobs and dictations apart."""
    SUMMARY["credits"] = {"month": 50, "month_used": 6, "month_left": 44, "bought_left": 10}
    try:
        _page_, words = _page(shape, monkeypatch, "account")
    finally:
        del SUMMARY["credits"]
    assert "Credits" in words and "6 / 50 used" in words and "44 left this month · 10 added" in words
    assert not {"Questions", "Agent jobs", "Dictation"} & set(words)


def test_her_answer_on_the_circle_step_is_one_short_bubble(qapp, monkeypatch):
    """31 Oct: "when Tora answers, that page gets too full and messy"."""
    import ui.onboarding_shape as O
    import ui.onboarding_steps as St
    from tests.agents.test_e2e_onboarding_steps import FakeVoice, throw_away
    monkeypatch.setattr(O, "animations_on", lambda: False)
    monkeypatch.setattr(St, "Interview", FakeVoice)
    p = O.OnboardingPanel([St.CircleStep])
    try:
        from PyQt6.QtCore import QRectF
        p.open_from(QRectF(900, 1000, 46, 10))
        step = p.step()
        step._asked = True
        step._event("tora", "That's the quarterly ")          # no wall of streaming words
        assert not step.reply.isVisibleTo(p) and step.status.text() == "Answering…"
        step._event("tora_end", "That's the quarterly revenue chart. " * 20)
        assert step.reply.isVisibleTo(p) and step.reply.objectName() == "obHers"
        assert len(step.reply.text()) <= St.REPLY_CHARS + 1 and step.reply.text().endswith("…")
        step.hold(True)                                        # asking again: the old answer goes
        assert not step.reply.isVisibleTo(p)
    finally:
        throw_away(p)


def test_a_tab_glides_in_inside_the_open_shape(shape, qapp, monkeypatch):
    """31 Oct: "when we press the shortcut from the bottom, can the screen
    animate inside" - New job + (and the tabs, and Ctrl, Ctrl) used to jump."""
    _settled_then_animating(shape, monkeypatch)
    home = shape.stack.contentsRect().topLeft()
    shape._new_job()                                      # the foot's New job +
    page = shape.stack.currentWidget()
    effect = page.graphicsEffect()
    assert effect is not None and effect.opacity() < 0.5 and page.pos().x() > home.x(), "from the right"
    _finish(page)
    assert page.graphicsEffect() is None and page.pos() == home, "settled, drawn plainly"


def test_tabs_tapped_quickly_never_leave_a_page_off_its_place(shape, qapp, monkeypatch):
    _settled_then_animating(shape, monkeypatch)
    for key in ("chat", "agents", "chat", "home", "agents", "home"):
        shape.show_tab(key)                               # each tapped before the last has settled
    for page in shape._pages.values():
        _finish(page)
    home = shape.stack.contentsRect().topLeft()
    for page in shape._pages.values():
        assert page.pos() == home and page.graphicsEffect() is None


def test_settings_pages_glide_in_with_the_tabs_tick(shape, qapp, monkeypatch):
    """31 Oct: "the switching tabs is good - do that for the tabs inside
    Settings too"."""
    from ui import sounds
    played = []
    monkeypatch.setattr(sounds, "ui", lambda event: played.append(event) or True)
    shape.open_from("bottom", _pill_at("bottom"))
    shape.show_settings("account")                         # at once: animations are still off
    assert shape.mode == "settings" and shape._t >= 1.0
    from ui import shape as S
    monkeypatch.setattr(S, "animations_on", lambda: True)
    monkeypatch.setattr(type(shape), "_left", lambda self: None)
    home = shape.settings_stack.contentsRect().topLeft()
    played.clear()
    shape.select("voice")                                  # further down the sidebar
    page = shape.settings_stack.currentWidget()
    assert played == ["tab"] and page.graphicsEffect() is not None and page.pos().y() > home.y()
    _finish(page)
    assert page.graphicsEffect() is None and page.pos() == home
    shape.select("account")                                # back up: from above
    page = shape.settings_stack.currentWidget()
    assert page.pos().y() < home.y()
    _finish(page)
