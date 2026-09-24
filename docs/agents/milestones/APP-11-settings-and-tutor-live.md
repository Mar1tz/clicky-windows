# APP-11 — the Settings and Tutor pages are live

**Status: done.** 22 new tests, 1065 passing.

## The problem

Both pages were drawings (M29). Their switches did nothing, the microphone
read "Realme Buds" on every PC, and the hotkey was typed in by hand. Some
controls stood for features that do not exist:

* "What she may see" — no code has ever read that choice;
* "Answer in my language" — Tora is English-only;
* "Draw on screen" — there is no such switch;
* "Start Tora when Windows starts" — nothing implemented it.

## What was built

**Tutor.** Slow mode, auto-advance, quiz mode, code mode, privacy guard,
OCR and web search each show the real value and change it through the
tray's own path, so they are saved too (APP-03). Quiz mode says honestly
that it lasts until Tora restarts. The dead controls are replaced by the
real **Instructions for Tora** box, the same thing as the tray's
"Instructions for Tora…", saved through the tray's own signal.

**Settings:**

* **Agent rails:** the workforce's own settings widget is embedded, not
  re-implemented. It already saves to config and applies to the running
  agents. Its Close button and duplicate title are hidden inside the page.
  If the agents did not start, the page says so.
* **Desktop actions you allowed:** the real standing grants (DC-16), each
  with **Forget**, or "Nothing is allowed without asking".
* **Microphone:** the real input devices, with the one in use selected.
  Picking one goes through the tray. That also fixed a tray bug: after a
  pick, the menu kept ticking the system default.
* **Always listening:** the real wake-word switch. The real hotkey is shown.
* **The floating panel**, and **Start Tora when Windows starts**, which is
  now real: a per-user `Run` entry (`ui/autostart.py`), with no admin
  rights needed. It points at the exe when packaged, and at `pythonw
  main.py` from source. An entry left by another copy of Tora is not
  mistaken for this one.

Neither page redraws while it is open, only when it is opened, so a
background change can never wipe something being typed. The Home page
still follows everything live.

## Proof

`tests/agents/test_e2e_app_settings.py` (22):

* every Tutor switch shows the real value and changes it;
* nothing that does nothing is offered;
* instructions save through the tray;
* grants are listed and can be forgotten;
* the real microphones are listed and one can be picked;
* the settings switches are real;
* the page says so when the agents did not start;
* the real agent settings are embedded, without Close;
* starting with Windows writes and removes the `Run` value, using a fake
  registry;
* the tray ticks the picked microphone;
* the bridge sends settings down the tray's path;
* forgetting a grant revokes it in the workforce.

Both pages were rendered with real fonts and checked.
