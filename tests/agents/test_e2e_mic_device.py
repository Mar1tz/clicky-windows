"""The chosen microphone survives devices moving (2 Oct 2026).

Windows numbers audio devices by their place in a list. A headset or a
monitor coming or going moves every number after it, and a saved
MIC_DEVICE_INDEX=9 ended up pointing at a monitor's speakers: every ask
failed with "Not an input device". The name is saved with the number now,
and the number is looked up again (config.Config.mic_device).
"""
from __future__ import annotations

import pytest

from config import cfg


def _devices(*names_and_inputs):
    return [{"name": n, "max_input_channels": i, "max_output_channels": 0 if i else 2}
            for n, i in names_and_inputs]


@pytest.fixture
def sd(monkeypatch):
    sounddevice = pytest.importorskip("sounddevice")
    state = {"list": []}
    monkeypatch.setattr(sounddevice, "query_devices",
                        lambda i=None: state["list"] if i is None else state["list"][i])
    monkeypatch.setenv("MIC_DEVICE_INDEX", "")           # restored after the test
    monkeypatch.setenv("MIC_DEVICE_NAME", "")
    monkeypatch.setattr(cfg, "mic_device_index", None)
    return state


def test_nothing_chosen_is_the_system_default(sd):
    sd["list"] = _devices(("Microphone (Realtek)", 2))
    assert cfg.mic_device() is None


def test_the_chosen_mic_is_found_again_after_the_list_moves(sd):
    sd["list"] = _devices(("Mapper", 2), ("Headset Mic", 1), ("Microphone (Realtek)", 2))
    cfg.set_mic_device(2)
    assert cfg.mic_device() == 2
    # A monitor's speakers arrive ahead of it: the mic is now number 3.
    sd["list"] = _devices(("Mapper", 2), ("Headset Mic", 1), ("DELL ST2220L", 0),
                          ("Microphone (Realtek)", 2))
    assert cfg.mic_device() == 3


def test_a_number_that_now_points_at_speakers_falls_back_to_the_default(sd):
    sd["list"] = _devices(("Mapper", 2), ("Microphone (Realtek)", 2), ("DELL ST2220L", 0))
    cfg.mic_device_index = 2                             # an old .env: a number, no name
    assert cfg.mic_device() is None, "never open speakers as a microphone"


def test_a_chosen_mic_that_was_unplugged_falls_back_to_the_default(sd):
    sd["list"] = _devices(("Mapper", 2), ("Headset Mic", 1))
    cfg.set_mic_device(1)
    sd["list"] = _devices(("Mapper", 2), ("Speakers", 0))
    assert cfg.mic_device() is None
