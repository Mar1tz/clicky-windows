# GW-12 — when the guide cannot tell, it asks, once

**Status: done.** 5 new tests, 811 passing.

## The symptom

Google Meet, 21 September — one step:

```
screen changed — not done yet (the meeting screen is still loading)
screen changed — not done yet ('Add others' visible but not clicked)
screen changed — not done yet (the camera was turned off instead)
step done — vision: the 'Add people' modal is now open
```

Something was clearly happening, the vision model kept saying it was not
the step, and the person heard nothing. The only thing that would have
spoken was the nudge a full minute in — "Take your time. Say 'next' when
you've done it" — which is the wrong message for someone actively trying.

## What was built

* `StepWatcher` counts consecutive "not done yet" answers (`not_yet`) and
  keeps the last reason. After two in a row — each on a screen that *had*
  changed — it is `unsure`. A "done" answer clears it.
* When a step becomes unsure, the manager — **once per step** —
  1. points at the target again, because the likeliest story is that the
     person did something *near* the step but not the step, and the arrow
     shows them where it actually is;
  2. says: *"I can't quite tell if that worked. If it did, say next and
     I'll move on."*
* It **never advances on a guess.** Asking is not moving; a "yes" is a
  "next", which the per-turn guide context already routes to `guide_step`.
* The summary line counts it: `asked N`.

## Proof

`tests/agents/test_e2e_guide_unsure.py` (5): one "no" is not unsure, two
is; a "yes" clears it; an unsure step asks, points again and does not
advance; and asks only once, not on every poll.
