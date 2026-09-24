# GW-05 — plan against the app's real control names

**Status: done, and demonstrated live.** 9 new tests, 748 passing.

## The symptom

52 fallbacks to the vision model across fourteen walkthroughs. Each one is
two to four seconds of silence and a paid screenshot.

## The cause, shown live

The planner named each target by *reading the screenshot*, and the pointer
then *searched* for that reading. When the reading was not the app's exact
label, the local tiers missed.

Measured on this machine with the real planner model, the real Calculator
and the same screenshot, with and without the app's own labels:

```
'work out 12 plus 7'  WITHOUT real labels   1/5 targets real
   [MISS] '1'    [MISS] '2'    [MISS] '+'    [MISS] '7'    [ok] 'Equals'

'work out 12 plus 7'  WITH real labels      5/5 targets real
   [ok] 'One'    [ok] 'Two'    [ok] 'Plus'   [ok] 'Seven'  [ok] 'Equals'
```

The model read the button faces. Calculator calls them *One, Two, Plus,
Seven*. Four of five targets would have gone to the vision model. That is
the 52.

Planning was also **faster** with the labels: 4.0–4.1 s against 4.9–5.1 s.

## What was built

* `_foreground_controls(title)` reads the window the user is in through
  UC-04's UI Automation walk — enabled, named, each once. 0.2–0.4 s,
  nothing uploaded.
* `gemini_planner.plan(controls=…)` puts them in the prompt, capped at 120,
  and the system prompt says the list is the truth, not the screenshot:
  a step acting on the current screen must copy a name *character for
  character*.
* `snap_targets` corrects a planned name that is unmistakably one of the
  real ones (`"Check for updates"` → `"Check for Updates..."`, case-folded
  exact matches, fuzzy matches ≥ 0.85) and **leaves alone** a later step's
  target that is not on screen yet — "Scientific" inside a menu that is not
  open must not be "corrected" into some current control that shares a word.
* The log says what was planned against:
  `planning 'multiply 5 by 5' against 36 on-screen controls`.

## Proof

`tests/agents/test_e2e_guide_plan.py` (9) — the labels reach the prompt,
a long list is capped, near-misses snap, exact labels and not-yet-visible
targets are left alone, the manager reads only enabled named controls, and
no tree means no list rather than a crash.

Plus the live run above, against the real model and the real app.
