"""19 Oct 2026, the owner: "I can't hear her."

Headphones became the default after HeyTora started, and she kept talking
into the laptop's speakers: PortAudio lists the devices once, and her output
stays open for the whole run. Now a change of Windows' default speaker or
microphone reopens the audio there, at the next quiet moment.
"""
from __future__ import annotations

from types import SimpleNamespace

import companion_manager as cm


def _hub(state=cm.AppState.IDLE):
    m = cm.CompanionManager.__new__(cm.CompanionManager)
    m._state, m._key_down = state, False
    m.events = []
    m._listener = SimpleNamespace(stop=lambda: m.events.append("mic closed"),
                                  start=lambda: m.events.append("mic opened"), _device=7)
    player = SimpleNamespace(stop=lambda: m.events.append("speaker closed"),
                             open=lambda: m.events.append("speaker opened"))
    m._realtime = SimpleNamespace(_player=player)
    m.sig_error = SimpleNamespace(emit=lambda *_a: m.events.append("error"))
    return m


def test_a_new_default_device_reopens_the_mic_and_her_speaker(monkeypatch):
    import sounddevice as sd
    m = _hub()
    monkeypatch.setattr(sd, "_terminate", lambda: m.events.append("list dropped"))
    monkeypatch.setattr(sd, "_initialize", lambda: m.events.append("devices listed again"))
    monkeypatch.setattr(cm.cfg, "mic_device", lambda: 3)
    monkeypatch.setattr(cm, "_sound_devices", lambda: ("Headphones (Neptune)", "Headset (Neptune)"))
    m._audio_devices_changed(("speakers", "mic"), ("headphones", "mic"))
    assert m.events == ["mic closed", "speaker closed", "list dropped", "devices listed again",
                        "mic opened", "speaker opened"]
    assert m._listener._device == 3, "the chosen mic found again by its name"


def test_it_waits_for_her_to_finish(monkeypatch):
    m = _hub(state=cm.AppState.SPEAKING)
    m.AUDIO_QUIET_WAIT_S = 0.6
    reopened = []
    m._reopen_audio = lambda: reopened.append(cm.time.monotonic())
    started = cm.time.monotonic()
    m._audio_devices_changed(("a", "b"), ("c", "b"))
    assert reopened and reopened[0] - started >= 0.5, "not in the middle of an answer"


def test_the_tray_mic_switch_keeps_the_wake_word(monkeypatch):
    m = _hub()
    made = {}

    class Listener:
        def __init__(self, **kw):
            made.update(kw)

        def start(self):
            pass
    monkeypatch.setattr("audio.ambient_listener.AmbientListener", Listener)
    monkeypatch.setattr(cm.cfg, "set_mic_device", lambda i: None)
    monkeypatch.setattr(cm.cfg, "mic_device", lambda: 2)
    m._handle_level = m._handle_wake = lambda *a: None
    m._transcribe_blocking = lambda pcm: ""
    m.set_mic_device(2)
    assert made.get("wake_transcriber") is not None and "wake_mode" in made
