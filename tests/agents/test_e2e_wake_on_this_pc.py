"""HP-08 (19 Oct 2026): the wake word heard on this PC.

Every speech segment near the mic went to Google to be checked for "Tora" -
1,889 checks in half a month, twice an hour one timing out. Now a keyword
spotter hears it here first: "Hey Tora" wakes at once, a bare name or a
near miss is checked in the cloud as before, and everything else is never
sent. The clips are Windows' own voice (System.Speech), 16 kHz.
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from audio import ambient_listener as al

FIXTURES = Path(__file__).parent / "fixtures"


def _pcm(name: str) -> bytes:
    with wave.open(str(FIXTURES / name)) as w:
        return w.readframes(w.getnframes())


class _Local:
    available = True

    def __init__(self, heard):
        self.heard = heard

    def hears(self, _pcm):
        return self.heard


def _listener(heard, mode="hybrid", transcript="hey tora"):
    events = {"woke": 0, "asked": 0}

    def transcribe(_pcm):
        events["asked"] += 1
        return transcript

    lis = al.AmbientListener.__new__(al.AmbientListener)
    lis._wake_local = _Local(heard) if heard != "absent" else None
    lis._wake_mode = mode
    lis._wake_transcriber = transcribe
    lis._wake_inflight = True
    lis._on_wake = lambda: events.__setitem__("woke", events["woke"] + 1)
    return lis, events


@pytest.mark.parametrize("heard, mode, woke, asked", [
    ("wake", "hybrid", 1, 0),        # "Hey Tora": at once, nothing sent
    (None, "hybrid", 0, 0),          # nothing like the name: nothing sent
    ("maybe", "hybrid", 1, 1),       # a bare "Tora": the cloud decides, as before
    ("maybe", "local", 0, 0),        # local only: never the cloud
    ("wake", "cloud", 1, 1),         # cloud chosen: every segment checked, as before 19 Oct
    ("absent", "hybrid", 1, 1),      # no spotter installed: as before
])
def test_who_decides(heard, mode, woke, asked):
    lis, events = _listener(heard, mode)
    lis._check_wake(b"\x00\x00" * 1600)
    assert (events["woke"], events["asked"]) == (woke, asked)
    assert lis._wake_inflight is False


def test_the_real_spotter_wakes_on_hey_tora_and_not_on_the_total():
    pytest.importorskip("sherpa_onnx")
    from audio.wake_local import LocalWake
    wake = LocalWake()
    assert wake.available, "model and keywords ship in assets/wake"
    assert wake.hears(_pcm("wake_hey_tora.wav")) == "wake"
    assert wake.hears(_pcm("wake_the_total.wav")) is None


def test_a_missing_model_falls_back_to_the_cloud(tmp_path):
    from audio.wake_local import LocalWake
    assert LocalWake(tmp_path).available is False


def test_every_wake_check_runs_on_one_worker():
    """ST-07: a thread per speech segment came and went all day."""
    import threading
    import time
    lis, events = _listener("wake")
    lis._wake_inflight = False
    seen = []
    real = lis._check_wake

    def check(pcm):
        seen.append(threading.current_thread().name)
        real(pcm)
    lis._check_wake = check
    for _ in range(3):
        lis._dispatch_wake_check(b"\x00\x00" * 1600)
        deadline = time.monotonic() + 2
        while lis._wake_inflight and time.monotonic() < deadline:
            time.sleep(0.01)
    assert seen == ["wake-check"] * 3 and events["woke"] == 3


def test_what_became_of_the_segments_is_said_every_half_hour(caplog):
    import logging
    lis, _events = _listener(None)
    lis.WAKE_TALLY_S = 0.0
    with caplog.at_level(logging.INFO, logger="tora.wake"):
        lis._check_wake(b"\x00\x00" * 1600)
    line = next(r.getMessage() for r in caplog.records if "speech segments" in r.getMessage())
    assert "1 speech segments - 0 woke Tora here, 0 checked in the cloud, 1 never sent" in line
