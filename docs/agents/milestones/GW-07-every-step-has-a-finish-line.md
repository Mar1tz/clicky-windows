# GW-07 — every step has a finish line, and step one's is not already crossed

**Status: done.** 12 new tests, 760 passing. `TargetGone` checked read-only
against the real window tree.

## The symptom

On the last step of the VS Code walkthrough, and twice in the YouTube one:

```
every expectation signal (control_gone) was already true at step start —
watching clicks and the screen instead
```

The planner describes each step's *outcome*; sometimes that outcome is
already the state of the screen. A signal that is already true cannot tell
"the user did it" from "it was always like that", so the step lost its
cheap, instant finish line and fell back to waiting for a click inside the
target or asking the vision model.

## What was checked and turned out fine

A theory was that vision-found targets came back without a bounding box,
switching click detection off. They do not — `_point_step_target` already
draws a 28 px box around a vision point. Checked before changing anything;
GW-03's new `target … found via …` lines will show what really happens in
the next field session.

## What was built

**Step one is repaired before it is spoken** (`repair_first_step`). It is
the only step whose starting screen is known when the plan arrives, so its
provably-true signals are dropped and logged: a `control_appears` already
in the control list, a `window_title` the user is already looking at, the
`process` already in front. Only *provable* truth counts — a name missing
from the capped control list is not proof it is absent, so `control_gone`
is never "repaired" on that evidence. Later steps are armed signal by
signal when they start, as before.

**A last-resort finish line** (`TargetGone`), used only when nothing of
the planner's survives arming: the step's own target disappearing.
Clicking a menu item closes the menu; confirming a dialog closes the
dialog. Two guards keep it honest:

* it arms only if the target is on screen when the step starts, so "gone"
  is a real transition and not a name the planner got wrong;
* it never fires when a different window is in front, because
  `control_exists` reads the *foreground* window and Alt-Tab would
  otherwise look exactly like the target vanishing.

A live planner signal is always preferred to it; the click on the target
and the screen-change check still run alongside it.

## Proof

`tests/agents/test_e2e_guide_signals.py` (12): the fallback fires on the
target disappearing, not on a window switch, never arms for an imaginary
target; the watcher uses it only when the planner's signals are all stale
or absent and prefers a live planner signal; step one's provably-true
signals are dropped and nothing else is touched.
