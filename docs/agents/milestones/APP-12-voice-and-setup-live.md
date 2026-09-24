# APP-12 — the Voice & models and Setup pages are live

**Status: done.** 17 new tests, 1082 passing.

## The problem

Both pages were drawings (M29):

* a voice picker with one option;
* model pickers that picked nothing;
* three agent endpoints typed in by hand;
* keys shown as "Connected" whatever their state;
* diagnostics that read "Realme Buds · 16 kHz" and "3 monitors · DPI 1.5"
  on every PC.

## What was built

**Voice & models:**

* **Spoken voice:** the real voices, with the one in use selected. A pick
  goes through the tray (`pick_voice`), which saves it and restarts the live
  session in that voice.
* **Conversation:** Live or Classic, the real route, through `pick_route`.
* **Models:** what each model actually does, in words ("Quick look ·
  walkthrough planner — fast; plans step one first") next to the real model
  ID. There are no pickers, because models are set in `.env`. **Refresh
  model list** is real.
* **Agent endpoints:** the workforce's own endpoint editor, the one behind
  "Connected accounts…", is embedded. It adds, tests and removes endpoints
  and sets the routing, and it applies to the live agents on Save.

**Setup:**

* **API keys:** whether each key is set, identified only by its last four
  characters. A key Google has refused, or one whose credits have run out,
  says so (APP-02) instead of "Connected". **Change keys…** opens the real
  keys dialog.
* **Diagnostics:** the real microphone, the live session (open, closed,
  refused or no key), the agent workforce, the screens and their scaling,
  and local OCR. All local: nothing on the page makes a request.
* **Run the setup wizard again**, **Save diagnostics report** and **Quit
  Tora** do exactly what the tray's items do.

**Two tray bugs fixed along the way:** after picking a voice or a route,
the tray's menu kept showing the old one until something else rebuilt it.

## Proof

`tests/agents/test_e2e_app_voice_setup.py` (17):

* the voice in use is selected and can be changed, and so can the route;
* the real models are shown and the list refreshes;
* the real endpoint editor is embedded, without Close, and says why when
  the agents are missing;
* keys show their real state, never the key, and a refused key is not
  called connected;
* the diagnostics are the real ones, local and honest;
* every Setup button does what the tray's does;
* the tray shows a new voice or route straight away.

Both pages were rendered with real fonts and checked.
