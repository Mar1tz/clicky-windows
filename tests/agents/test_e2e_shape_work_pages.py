"""Integrations and Routines in the shape's Settings (29 Oct 2026, step 5b).

Both were reachable only by voice: "connect Notion", and a skill's "Runs by
itself" line. The pages show them and use the same ways in - the
companion's own connect / disconnect tools, the skills' own switch.
"""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _wait(qapp, until, s=3.0):
    end = time.monotonic() + s
    while not until() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return until()


# ── the bridge ─────────────────────────────────────────────────────────────

def test_what_is_connected_and_what_can_be(monkeypatch):
    from integrations import catalog
    from integrations import connections as conns_mod
    from ui.app_bridge import AppBridge
    gmail = SimpleNamespace(provider="gmail", account="me@example.com",
                            state=SimpleNamespace(value="connected", usable=True))
    monkeypatch.setattr(conns_mod, "connections", lambda: SimpleNamespace(all=lambda: [gmail]))
    hub = SimpleNamespace(servers={"linear": SimpleNamespace(running=True, tools=[1, 2, 3])}, failed={})
    br = AppBridge(manager=SimpleNamespace(_mcp_hub=lambda: hub,
                                           sig_state_changed=SimpleNamespace(connect=lambda f: None)))
    data = br.integrations()
    assert data["running"]
    assert {"id": "gmail", "name": "Gmail", "detail": "me@example.com · connected", "ok": True} in data["connected"]
    assert {"id": "linear", "name": "Linear", "detail": "3 tools", "ok": True} in data["connected"]
    offered = {a["id"] for a in data["available"]}
    assert "calendar" in offered and "notion" in offered
    assert "gmail" not in offered and "linear" not in offered, "connected ones are not offered again"
    assert offered >= set(catalog.SERVICES) - {"linear"}


def test_routines_are_the_skills_that_run_by_themselves(monkeypatch):
    from ui.app_bridge import AppBridge
    br = AppBridge()
    monkeypatch.setattr(br, "skills", lambda: [
        {"id": "inbox", "name": "Check my inbox", "every": "weekdays 09:00", "enabled": True, "last": None},
        {"id": "pdf", "name": "Convert to PDF", "every": "", "enabled": True, "last": None}])
    sched = SimpleNamespace(jobs=lambda: [{"id": "skill:inbox", "next": "2026-10-30T09:00", "last_fired": 100.0}])
    monkeypatch.setattr(br, "workforce", lambda: SimpleNamespace(scheduler=sched))
    assert br.routines() == [{"id": "inbox", "name": "Check my inbox", "every": "weekdays 09:00", "enabled": True,
                              "next": "2026-10-30T09:00", "last_fired": 100.0, "last": None}]


# ── the Integrations page ──────────────────────────────────────────────────

class _Work:
    def __init__(self):
        self.calls = []
        self.answers = {}

    def install(self, br, monkeypatch, connected=()):
        monkeypatch.setattr(br, "integrations", lambda: {
            "connected": list(connected), "running": True,
            "available": [{"id": "github", "name": "GitHub", "how": "key", "gives": "repositories"},
                          {"id": "notion", "name": "Notion", "how": "sign_in", "gives": "pages"}]})

        def connect(service, pasted=False):
            self.calls.append(("connect", service, pasted))
            return self.answers.get((service, pasted), {"ok": True})
        monkeypatch.setattr(br, "connect_integration", connect)
        monkeypatch.setattr(br, "disconnect_integration",
                            lambda service: self.calls.append(("disconnect", service)) or {"ok": True})


def _page(kind, qapp, monkeypatch, **kw):
    from ui.app_bridge import AppBridge
    from ui import shape_settings
    br = AppBridge()
    work = _Work()
    work.install(br, monkeypatch, **kw)
    page = getattr(shape_settings, kind)(br)
    page.resize(620, 600)
    page.show()
    qapp.processEvents()
    return page, work


def _button(page, text):
    from PyQt6.QtWidgets import QApplication, QPushButton
    QApplication.processEvents()        # a redrawn page's widgets show a moment after
    return next(b for b in page.findChildren(QPushButton) if b.text() == text and b.isVisibleTo(page))


def test_a_service_with_a_key_waits_for_it_on_the_clipboard(qapp, monkeypatch):
    page, work = _page("IntegrationsPage", qapp, monkeypatch)
    work.answers[("github", False)] = {"ok": True, "waiting_for_copy": "github"}
    _button(page, "Connect").click()                     # GitHub is first
    assert _wait(qapp, lambda: page._waiting)
    assert ("connect", "github", False) in work.calls
    _button(page, "I've copied it").click()
    assert _wait(qapp, lambda: ("connect", "github", True) in work.calls)
    assert _wait(qapp, lambda: not page._waiting and "Tora tells you" in page._said)


def test_a_sign_in_says_where_it_happens(qapp, monkeypatch):
    from PyQt6.QtWidgets import QPushButton
    page, work = _page("IntegrationsPage", qapp, monkeypatch)
    connect = [b for b in page.findChildren(QPushButton) if b.text() == "Connect"]
    connect[1].click()                                   # Notion
    assert _wait(qapp, lambda: "sign in in your browser" in page._said)


def test_an_error_is_said_as_it_is(qapp, monkeypatch):
    page, work = _page("IntegrationsPage", qapp, monkeypatch)
    work.answers[("github", False)] = {"error": "Tora's jobs are not running, so nothing can be connected right now."}
    _button(page, "Connect").click()
    assert _wait(qapp, lambda: page._said.startswith("Tora's jobs are not running"))


def test_disconnect(qapp, monkeypatch):
    page, work = _page("IntegrationsPage", qapp, monkeypatch,
                       connected=[{"id": "gmail", "name": "Gmail", "detail": "me@example.com · connected", "ok": True}])
    _button(page, "Disconnect").click()
    assert _wait(qapp, lambda: ("disconnect", "gmail") in work.calls)
    # Until the page has the answer: thrown away with it still on its way
    # back from the worker thread, the next test's events crashed Qt.
    assert _wait(qapp, lambda: page._busy is None)


# ── the Routines page ──────────────────────────────────────────────────────

def test_routines_list_when_each_runs_and_switch_off(qapp, monkeypatch):
    from PyQt6.QtWidgets import QLabel
    from ui.app_bridge import AppBridge
    from ui.app_widgets import Switch
    from ui.shape_settings import RoutinesPage
    br = AppBridge()
    switched = []
    monkeypatch.setattr(br, "routines", lambda: [
        {"id": "inbox", "name": "Check my inbox", "every": "weekdays 09:00", "enabled": True,
         "next": "2026-10-30T09:00", "last_fired": 0.0, "last": None}])
    monkeypatch.setattr(br, "set_skill_enabled", lambda i, on: switched.append((i, on)))
    page = RoutinesPage(br)
    words = " ".join(l.text() for l in page.findChildren(QLabel))
    assert "Check my inbox" in words and "Every weekdays 09:00 · next Fri 30 Oct, 09:00" in words
    page.findChild(Switch).toggled.emit(False)
    assert switched == [("inbox", False)]


def test_no_routines_says_how_to_make_one(qapp, monkeypatch):
    from PyQt6.QtWidgets import QLabel
    from ui.app_bridge import AppBridge
    from ui.shape_settings import RoutinesPage
    br = AppBridge()
    monkeypatch.setattr(br, "routines", lambda: [])
    opened = []
    page = RoutinesPage(br)
    monkeypatch.setattr(page, "edit_skill", lambda skill=None: opened.append(skill))
    words = " ".join(l.text() for l in page.findChildren(QLabel))
    assert "Runs by itself" in words and "every: weekdays 9am" in words
    _button(page, "New routine").click()
    assert opened == [None], "the skill editor, for a new one"
