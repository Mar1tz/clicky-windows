# LG-07, LG-08, LG-09 — one thing per step, done the moment it is done, and measured

**Status: done.** Plan: `docs/agents/LIVE_GUIDE_PLAN.md`. 24 new tests,
1240 passing. Five mutations, each caught by the test written for it.

## LG-07 · Every step has something to point at, and one thing to do

This is from the 24 Sep 23:02 plan. It is fixed locally in
`split_compound_steps`, which runs on both the streamed first step and the
full plan.

* **A click and then typing becomes two steps.** "Click the search bar on
  Takealot, type laptops, and press Enter" becomes:
  * "Click the search bar on Takealot."
  * "Type laptops, and press Enter."

  Both point at the same field, so the arrow stays on it.
* **A typing step points at the field it names.** "Type takealot.com into
  the address bar and press Enter" had no target. It now points at
  "address bar": the browser's field is "Address and search bar", which the
  pointer matches.
* **Unchanged:** a named target is never overwritten; "Type youtube.com and
  press Enter" (one intent, no field named) stays one step; wait steps get
  no target; the existing split of two pointing actions is untouched.

**Not done yet, needs a yes:** telling the planner itself to name a
target on every action step and to write one action per step. It is a
prompt change, proved by re-planning tonight's two walkthroughs, which is
two to four planner calls, about $0.05.

## LG-08 · A shortcut step is done the moment its keys are pressed

"Press Ctrl and T together to open a new tab" was seen done by a 4 s vision
check (23:03:08 → :12).

* **The shortcut is read from the step's words.** `shortcut_in` turns
  them into a key combination: "ctrl+t", "ctrl+shift+n", "alt+tab",
  "enter", "f5", "windows". "Press the Export button" is not a shortcut.
* **The keys finish the step.** `KeysPressed` registers the combination
  with the global keyboard hook (the `keyboard` library Tora already uses
  for its hotkey) for as long as the step lasts. It never swallows the
  keys. The step is done 0.4 s after they are pressed, so what they do has
  begun.
* **The hook dies with the step.** The step watcher releases it when the
  step ends (`StepWatcher.end` → `close`).
* **Typing steps finish on Enter too.** "Type … and press Enter" steps now
  also finish on Enter, which is exactly the moment they are done.
* **No real hook in tests.** The test sandbox replaces the real one.

## LG-09 · Measured, every walkthrough

Every step now writes one line when it ends:

    guide step 2 timeline: arrow +0.4s (ahead uia), voice +1.2s

* **The arrow's time** is measured from the step going up, with the tier
  that found it.
* **"Ahead"** means LG-06 had it ready before the step.
* **"no arrow"** appears when a step with a target never got one.
* **"not said"** appears when the step was done before its line could
  play.

Every guide ends with a scorecard:

    guide scorecard: arrow p50 0.4s p90 2.1s (4, 2 found ahead), no arrow 1,
    not said 1, Windows voice, next step up p50 0.5s

These are the numbers `LIVE_GUIDE_PLAN.md`'s *live* table is checked
against, from the field, walkthrough by walkthrough.

## Proof

* `tests/agents/test_e2e_guide_one_thing.py` (19): LG-07 on tonight's
  wordings, plus LG-08's parser, signal, hook lifetime and wiring.
* `tests/agents/test_e2e_guide_scorecard.py` (5): LG-09's timeline line
  and scorecard.

Five mutations, each caught:
* click-then-type not split;
* typing steps not named;
* anything after "press" read as a key;
* a shortcut step not watching its keys;
* the keys outliving the step.
