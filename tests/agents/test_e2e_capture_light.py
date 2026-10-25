"""Capture you can see (WX-05, the Massive Direction Plan).

The flow bar lights red while a microphone stream is open and blue just
after the screen was looked at; and nothing is captured before the user
asks - in hotkey mode the microphone opens on the press and closes after.
Every capture path reports to capture_watch, so a new one cannot skip the
light without failing here.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import capture_watch

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _clean():
    capture_watch.reset()
    yield
    capture_watch.reset()


class FakeStream:
    started = 0

    def __init__(self, *a, **kw):
        pass

    def start(self):
        FakeStream.started += 1

    def stop(self):
        pass

    def close(self):
        pass


def test_hotkey_mode_opens_the_microphone_only_while_asked(monkeypatch):
    from audio import ambient_listener as al
    monkeypatch.setattr(al.sd, "InputStream", FakeStream)
    FakeStream.started = 0
    heard = []
    capture_watch.on_change(lambda on, at: heard.append(on))

    lis = al.AmbientListener(on_level=lambda r: None, on_wake=lambda: None, ambient=False)
    lis.start()
    time.sleep(0.05)                                   # the warm-up thread
    assert FakeStream.started == 0 and capture_watch.mic_opens == 0, "nothing before the press"
    assert not capture_watch.mic_open()

    lis.start_recording()
    assert capture_watch.mic_open() and heard == [True]
    lis.stop_recording()
    assert not capture_watch.mic_open() and heard == [True, False]
    lis.stop()


def test_a_look_at_the_screen_lights_up(monkeypatch):
    from screen import capture

    class Sct:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def grab(self, region):
            return SimpleNamespace(size=(32, 32), bgra=b"\x00" * 32 * 32 * 4)

    monkeypatch.setattr(capture.mss, "mss", Sct)
    assert capture_watch.screen_grabs == 0
    assert capture.capture_region(0, 0, 32, 32) is not None
    assert capture_watch.screen_grabs == 1 and capture_watch.screen_recent()


def test_the_flow_bar_draws_both_lights(qapp):
    from ui.flow_bar import SCREEN_LIT_S, FlowBar
    flow = FlowBar()
    try:
        now = time.monotonic()
        flow.set_capture(True, now)
        assert flow.lights(now) == (True, True)
        assert flow.lights(now + SCREEN_LIT_S + 0.1) == (True, False), "the screen light goes out"
        flow.set_capture(False, 0.0)
        assert flow.lights() == (False, False)
        flow.set_capture(True, time.monotonic())
        assert not flow.pill.grab().isNull()           # the paint path runs
    finally:
        flow.close()


def test_every_capture_path_reports_to_the_light():
    """A grab or an opened microphone stream anywhere in the app reports it.
    ui/ is left out: its microphone tests are the visible meter themselves."""
    skip = {"tests", "dist", "build", "ui", ".venv", "venv", "scratch"}
    missing = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in skip or path.name == "capture_watch.py":
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bsct\.grab\(", src) and "capture_watch.screen()" not in src:
            missing.append(f"{rel}: grabs the screen")
        if re.search(r"_stream\.start\(\)", src) and "sd.InputStream(" in src and "capture_watch.mic(" not in src:
            missing.append(f"{rel}: opens a microphone")
    assert not missing, missing
