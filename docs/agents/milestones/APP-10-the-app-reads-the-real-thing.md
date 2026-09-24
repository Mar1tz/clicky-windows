# APP-10 — the app reads the real thing: the bridge, the shell, Home

**Status: done.** 17 new tests, 1043 passing.

## The problem

The Tora app window was a drawing, as asked for in M29 ("place everything
where it needs to go, no functionality yet"). All nine pages showed
placeholder numbers: *$0.79 spent today*, *Realme Buds*, *Application
Builder step 4 of 10*. Every switch was inert.

## What was built

**One bridge, `ui/app_bridge.py`.** The window's only door into the running
app.

* **Reads** come from where the app already keeps things: the manager (state,
  live session, account problem, the running walkthrough), config, the
  journal, and the agent workforce. Nothing is copied, so nothing can go
  stale.
* **Changes** go down the tray's own path. `TrayManager.apply_switch`
  ticks the tray's menu and calls the same handler a click calls, so the
  tray and the app can never disagree, and `main.py` stays the one place
  that connects controls to handlers.
* Every method copes with missing pieces: no tray, no workforce, no manager.
  A window opened on its own shows nothing live, and reads nobody's journal.

**Live pages.** A page marked `LIVE` rebuilds its body from the bridge each
time it is shown, and when something it shows changes. Changes redraw the
visible page at most once per 150 ms burst. Pages not converted yet still
build once, as before.

**Home, live:**

* **What she is doing:** "Listening for 'Tora'", "Hold Ctrl + Win and
  talk", or "Thinking…", with the real microphone and the wake-word state.
* **Whether she can answer:** Connected, Not connected, Out of Gemini
  credit, or API key refused (APP-02).
* **Today, from the journal:** questions, walkthroughs, and what is due for
  review.
* **Happening now:** the running walkthrough ("Step 2 of 3 · Choose MP4 in
  the Format list") and the working agents with their progress, or
  "Nothing running right now".
* The sidebar's hotkey hint is the real hotkey.

**Two rendering bugs fixed on every page:**

* Pills drew a second, square border inside the rounded one. The `QFrame`
  selector matched the label inside, because a QLabel *is* a QFrame.
* Glowing dots were clipped into squares. The halo is twice the dot, but
  the widget was only 6 px bigger.

## Proof

`tests/agents/test_e2e_app_live.py` (17):

* **the bridge:** follows the state, reads the walkthrough (including one
  still being planned), the agents and today's journal counts, and reads no
  journal at all when on its own;
* **changes:** a switch goes through the tray, the tray changes it exactly
  as a click would, and "Open panel" asks the tray;
* **Home:** no placeholder text anywhere, "nothing running" when true, the
  walkthrough and the agents when not, and the four connection states;
* **the window:** a change redraws the visible page, once per burst, and
  the sidebar follows the state.

The page was rendered with real fonts and checked by eye.
