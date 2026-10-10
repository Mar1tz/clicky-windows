"""The top-right agent orbs (agents/ui/orbs.py): lifecycle, colour, what the
hover label says, the icons, the click, and the animation timer - which must
not run when nothing moves, and must keep running until a finished orb has
gone."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from agents.bootstrap import build_agent_manager  # noqa: E402
from agents.core import AgentSpec, AgentState, AgentStatus, TaskStatus  # noqa: E402
from agents.ui.orbs import GAP, ORB, WIDTH, AgentOrbs, Orb, kind_of, progress_colour  # noqa: E402

REQUEST = "Create a new folder called MyApp and build the foundation of a React application inside it."


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def st(name="Application Builder", **kw) -> AgentState:
    kw.setdefault("current_task_id", "t1")
    return AgentState(spec=AgentSpec(name=name, goal="g"), **kw)


def settle(orbs, frames=40):
    """Advance the animation by `frames` 16 ms frames, deterministically:
    back-dating the last-tick clock beats sleeping in a test."""
    for _ in range(frames):
        orbs._last -= 0.016
        orbs._tick()


# -- colour ------------------------------------------------------------------

def test_ring_colour_sweeps_blue_to_green():
    start, mid, end = progress_colour(0.0), progress_colour(0.5), progress_colour(1.0)
    assert start.blue() > start.green() > start.red()           # blue
    assert mid.green() > 180 and mid.blue() > 180                 # cyan
    assert end.green() > end.red() and end.green() > end.blue()   # green


def test_orb_colour_by_status():
    o = Orb(st(status=AgentStatus.RUNNING, progress=0.2))
    o.shown_progress = 0.2
    assert o.ring_colour() == progress_colour(0.2)
    o.update(st(status=AgentStatus.PAUSED, progress=0.2))
    assert o.ring_colour().red() > 200 and o.ring_colour().green() > 150     # amber
    o.update(st(status=AgentStatus.ERROR))
    assert o.ring_colour().red() > 200 and o.ring_colour().green() < 100     # red
    o2 = Orb(st(status=AgentStatus.RUNNING, progress=0.9))
    o2.update(st(status=AgentStatus.IDLE, progress=1.0))
    assert o2.done and o2.target == 1.0


# -- lifecycle ---------------------------------------------------------------

def test_orbs_appear_stack_and_vanish(qapp):
    orbs = AgentOrbs(None)
    assert not orbs.isVisible()
    a, b = st("A", status=AgentStatus.RUNNING), st("B", status=AgentStatus.RUNNING)
    orbs.on_agent_changed(a)
    orbs.on_agent_changed(b)
    assert [o.name for o in orbs.orbs] == ["A", "B"]
    assert orbs.isVisible()
    assert orbs.height() > ORB * 2 + GAP                        # stacked downward
    assert orbs._centre(1).y() - orbs._centre(0).y() == ORB + GAP

    a.status = AgentStatus.IDLE
    a.progress = 1.0
    orbs.on_agent_changed(a)
    assert orbs.orbs[0].done
    # holds for a moment with the tick, then leaves
    settle(orbs, 10)
    assert len(orbs.orbs) == 2
    orbs.orbs[0].finished_at -= 10                               # pretend the hold elapsed
    settle(orbs, 60)
    assert [o.name for o in orbs.orbs] == ["B"]


def test_finished_agents_never_appear(qapp):
    orbs = AgentOrbs(None)
    orbs.on_agent_changed(st(status=AgentStatus.IDLE, progress=1.0))
    assert orbs.orbs == [] and not orbs.isVisible()


def test_progress_eases_toward_target(qapp):
    orbs = AgentOrbs(None)
    s = st(status=AgentStatus.RUNNING, progress=0.0)
    orbs.on_agent_changed(s)
    orb = orbs.orbs[0]
    assert orb.shown_progress == 0.0
    s.progress = 0.8
    orbs.on_agent_changed(s)
    settle(orbs, 1)
    assert 0 < orb.shown_progress < 0.8, "the ring animates rather than jumping"
    settle(orbs, 80)
    assert abs(orb.shown_progress - 0.8) < 0.01


def test_timer_stops_when_nothing_moves(qapp):
    """An idle desktop must not pay 60 fps for a paused orb."""
    orbs = AgentOrbs(None)
    s = st(status=AgentStatus.PAUSED, progress=0.5)
    orbs.on_agent_changed(s)
    assert orbs._timer.isActive()
    settle(orbs, 120)
    assert not orbs._timer.isActive()
    running = st("R", status=AgentStatus.RUNNING, progress=0.5)
    orbs.on_agent_changed(running)
    settle(orbs, 120)
    assert orbs._timer.isActive(), "a running orb breathes, so it keeps animating"


def test_sits_in_the_top_right_of_the_primary_screen(qapp):
    orbs = AgentOrbs(None)
    orbs.on_agent_changed(st(status=AgentStatus.RUNNING))
    rect = QApplication.primaryScreen().availableGeometry()
    assert orbs.width() == WIDTH
    assert orbs.x() + orbs.width() <= rect.right() + 1
    assert orbs.x() > rect.right() - WIDTH - 20
    assert orbs.y() >= rect.top()


def test_click_reports_the_agent(qapp):
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    orbs = AgentOrbs(None)
    s = st(status=AgentStatus.RUNNING)
    orbs.on_agent_changed(s)
    got = []
    orbs.clicked.connect(got.append)
    c = orbs._centre(0)
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(c), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    orbs.mousePressEvent(ev)
    assert got == [s.id]


# -- driven by a real workforce ----------------------------------------------

def test_real_task_drives_an_orb_to_done(qapp, tmp_path):
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True).start()
    orbs = AgentOrbs(mgr)
    try:
        task = mgr.wait(mgr.submit(REQUEST).id, 60)
        assert task.status is TaskStatus.COMPLETED, task.error
        for _ in range(20):
            qapp.processEvents()
            settle(orbs, 1)
        assert len(orbs.orbs) == 1
        orb = orbs.orbs[0]
        assert orb.name == "Application Builder"
        assert orb.done
        settle(orbs, 60)
        assert abs(orb.shown_progress - 1.0) < 0.01
    finally:
        orbs.close()
        mgr.stop()


# -- 10 Oct 2026: "once the agent is done the thing doesn't disappear" ------------

def test_a_finished_card_leaves_by_itself(qapp, monkeypatch):
    """The timer stopped once the ring had filled, so the hold before fading
    was never checked again and the card stayed. It keeps running now."""
    import agents.ui.orbs as orbs_mod
    clock = [1000.0]
    monkeypatch.setattr(orbs_mod.time, "monotonic", lambda: clock[0])
    orbs = AgentOrbs(None)
    s = st(status=AgentStatus.RUNNING, progress=0.6)
    orbs.on_agent_changed(s)
    s.status, s.progress = AgentStatus.IDLE, 1.0
    orbs.on_agent_changed(s)
    for _ in range(100):                      # 1.6 s: the ring fills, the tick draws; nothing else moves
        clock[0] += 0.016
        orbs._tick()
    assert orbs.orbs and orbs._timer.isActive(), "still holding - and still ticking"
    clock[0] += orbs_mod.HOLD_DONE_S
    for _ in range(60):
        clock[0] += 0.016
        orbs._tick()
    assert orbs.orbs == [] and not orbs.isVisible(), "gone, on its own"
    assert not orbs._timer.isActive()


def test_the_card_says_what_it_is_doing():
    o = Orb(st(status=AgentStatus.RUNNING, progress=0.0, current_task_title="Build my website"))
    assert o.title() == "Build my website" and o.line() == "Planning the steps…"
    o.update(st(status=AgentStatus.RUNNING, progress=0.4, current_task_title="Build my website",
                current_action="Writing index.html"))
    o.shown_progress = 0.4
    assert o.line() == "Writing index.html"
    o.update(st(status=AgentStatus.IDLE, progress=1.0))
    assert o.title() == "Build my website", "the job's name stays when it ends"
    assert o.line() == "Done · click to see it"
    o2 = Orb(st(status=AgentStatus.RUNNING))
    o2.update(st(status=AgentStatus.ERROR))
    assert "click to see why" in o2.line()


def test_a_click_opens_that_job_and_a_finished_card_goes(qapp):
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    orbs = AgentOrbs(None)
    s = st(status=AgentStatus.RUNNING, current_task_id="task-42")
    orbs.on_agent_changed(s)
    s.status, s.progress, s.current_task_id = AgentStatus.IDLE, 1.0, None
    orbs.on_agent_changed(s)
    opened = []
    orbs.opened.connect(lambda agent, task: opened.append((agent, task)))
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(orbs._centre(0)), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    orbs.mousePressEvent(ev)
    assert opened == [(s.id, "task-42")], "the job it was doing, though the agent has moved on"
    assert orbs.orbs[0].leaving, "seen: it goes now"


def test_a_click_opens_the_apps_agents_page_on_that_job():
    """main.py wires the cards to tray.on_open_app_page("agents/job/<id>")."""
    import inspect
    from pathlib import Path

    from ui import app_window
    assert "agents/job/" in inspect.getsource(app_window.ToraApp.show_page)
    main = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    assert 'on_open=lambda _agent, task: tray.on_open_app_page.emit(' in main


def test_small_again_and_each_wears_its_kind():
    """10 Oct: "I want the agents to still be small circles" - the cards took
    too much of the screen."""
    assert WIDTH <= 100 and ORB <= 44
    assert kind_of("Application Builder") == "code" and kind_of("Research Agent") == "research"
    assert kind_of("File Agent") == "file" and kind_of("Terminal Agent") == "terminal"
    assert kind_of("Project Lead") == "spark"


def test_the_hover_label_never_catches_the_mouse(qapp):
    from PyQt6.QtCore import Qt
    orbs = AgentOrbs(None)
    orbs.on_agent_changed(st(status=AgentStatus.RUNNING, progress=0.4, current_task_title="Build my site"))
    orbs.orbs[0].hovered = True
    settle(orbs, 30)
    lab = orbs._label
    assert lab is not None and lab.isVisible()
    assert lab.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    orbs.orbs[0].hovered = False
    settle(orbs, 40)
    assert not lab.isVisible()
    orbs.close()
