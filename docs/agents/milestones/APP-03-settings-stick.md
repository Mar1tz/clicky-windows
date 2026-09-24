# APP-03 — settings stick

**Status: done.** 20 new tests, 1021 passing.

## The problem

* **Seven switches forgot themselves.** Privacy guard, OCR, web search,
  slow mode, code mode, the journal and walkthrough auto-advance were held
  only in memory. Every launch put them back to their defaults, whatever
  you had chosen. The tray kept its own separate copies, so it and the app
  started from two sets of defaults.
* **Two saved settings were saved in the wrong place.** Custom
  instructions and reply language were written to `.env` beside
  `companion_manager.py`. From source that is the right file. In the
  packaged `Tora.exe` it is inside `_internal\`, where nothing reads it, so
  in the installed app they were never really saved.
* **"Multilingual" said ON and did nothing.** Tora is English-only, and
  nothing ever read the switch.

## What was built

* `cfg.switch(name)` and `cfg.set_switch(name, on)` save the seven switches
  in `.env`, the same way the voice and the microphone mode already were.
  The manager starts from them, each tray setter saves what it changes, and
  the tray's menu starts from the same saved values. The overlay's slow
  mode is restored at start too.
* **Quiz mode is deliberately not saved.** It is something you start, and
  restoring it would open the next session with a quiz nobody asked for.
* Custom instructions and reply language are saved through
  `cfg.env_path()`, which is next to `Tora.exe` when packaged.
* The "Multilingual" item is gone from the tray menu. Its signal and setter
  remain, so nothing that connects to them breaks.
* **No test can touch the real `.env` any more.** The shared test sandbox
  now points every write at the test's own copy and clears the saved
  switches, so a developer's clicks cannot leak into a test.

## Proof

`tests/agents/test_e2e_settings_stick.py` (20):

* every switch survives a simulated restart;
* an unsaved switch is its default;
* each tray setter saves what it changes;
* quiz mode is never restored;
* the manager reads every switch at start;
* the tray shows the saved values and offers no "Multilingual";
* the other two settings are saved where they are read.

The real `.env` kept its 21 September timestamp through every run.
