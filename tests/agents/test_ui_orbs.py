"""The top-right agent orbs: lifecycle, colour, and the animation timer
that must not run when nothing moves."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from agents.bootstrap import build_agent_manager  # noqa: E402
from agents.core import AgentSpec, AgentState, AgentStatus, TaskStatus  # noqa: E402
from agents.ui.orbs import GAP, ORB, WIDTH, AgentOrbs, Orb, progress_colour  # noqa: E402

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
    assert orbs.x() > rect.right() - 120
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
