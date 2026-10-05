"""One HeyTora at a time (5 Oct 2026).

"Don't allow multiple of these open at once - when I click the app to open
on the home screen, just open the app UI instead of launching it again."
Two HeyTora.exe ran side by side on 4 Oct. Now the first copy listens on a
local socket; a second one asks it to open its window and quits. Real
sockets here, under a name of the test's own.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _until(qapp, done, timeout=5.0):
    end = time.monotonic() + timeout
    while not done() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return done()


def test_a_second_heytora_asks_the_first_to_open_and_goes(qapp):
    from ui.single_instance import SingleInstance
    name = f"HeyTora-test-{uuid.uuid4().hex[:8]}"
    first = SingleInstance(name)
    opened = []
    first.activated.connect(lambda: opened.append(1))
    try:
        assert first.claim() is True, "nothing running: this is the HeyTora"
        second = SingleInstance(name)
        assert second.claim(timeout_ms=1000) is False, "already running: the second one goes"
        assert _until(qapp, lambda: opened == [1]), "and the first one opens its window"
        third = SingleInstance(name)
        assert third.claim(timeout_ms=1000) is False
        assert _until(qapp, lambda: opened == [1, 1]), "every time"
    finally:
        first.release()


def test_after_letting_go_a_new_copy_is_the_one(qapp):
    from ui.single_instance import SingleInstance
    name = f"HeyTora-test-{uuid.uuid4().hex[:8]}"
    first = SingleInstance(name)
    assert first.claim()
    first.release()                                  # signing out: it restarts itself
    fresh = SingleInstance(name)
    try:
        assert fresh.claim(timeout_ms=500) is True
    finally:
        fresh.release()


def test_each_windows_user_has_their_own():
    from ui.single_instance import server_name
    name = server_name()
    assert name.startswith("HeyTora-") and all(ch.isalnum() or ch in "-_" for ch in name)


def test_bring_forward_raises_the_visible_windows():
    from ui.single_instance import bring_forward
    done = []

    def window(visible, minimised=False):
        return SimpleNamespace(isVisible=lambda: visible, isMinimized=lambda: minimised,
                               showNormal=lambda: done.append("restore"),
                               raise_=lambda: done.append("raise"),
                               activateWindow=lambda: done.append("focus"))
    bring_forward(lambda: [window(True, minimised=True), window(False)])
    assert done == ["restore", "raise", "focus"]


def test_main_claims_first_and_lets_go_before_restarting():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    claim = src.index("if not single.claim():")
    assert claim < src.index("upd.at_startup(") < src.index("door = sign_in_gate.gate()")
    assert src.index("single.release()") < src.index("        sign_in_gate.restart()")
    assert "_activate[0] = lambda: _open_app()" in src, "once up, a second start opens the app window"
