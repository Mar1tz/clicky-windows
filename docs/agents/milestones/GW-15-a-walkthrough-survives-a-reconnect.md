# GW-15 — a walkthrough survives a reconnect

**Status: done.** 6 new tests, 900 passing. Live: a blank session named
the steps actually taken 6 times in 6, up from 0 in 6.

## The claim, measured first

The plan said: *"could not resume the conversation" appears dozens of
times a day, and a fresh session only half-knows a guide is running.*
GW-06's premise turned out wrong once it was measured, so this one was
measured before anything was built.

The log has 15 failed resumes in 112 session opens over four days. One
fell inside a walkthrough (CapCut, 22 September, 23:40).

A live check replayed exactly what the app sends when a resume fails: the
recap (`_reseed`), then, with the next question, the real per-turn context
from `_screen_context`, with the walkthrough on step 2 of 3. Two questions
each, on a blank session:

| question | before |
|---|---|
| what step am I on? | right 2/2 |
| how many steps are left? | right 2/2 |
| okay, what now? | the current step, pointed at 2/2 |
| where were we? | the current step 2/2 |
| can you show me how to export it again? | `guide_step(where)` 2/2 — no restart |
| **what have I done so far?** | **inferred** — right only when the recap happened to mention it |

The model restarted the running walkthrough **0 times in 12**. It knows the
goal, the current step and the count, because the walkthrough paragraph
rides along with **every** question and is never shortened to "same
screen". The runner itself does not depend on the live session at all.

So "half-knows" was true in one narrow and real way: **it did not know what
was behind the user.** Steps are spoken in Tora's own voice, not the live
model's, so they are in no conversation history, whether the session is
resumed or not. On a plan nobody could guess:

```
guess 'what have I done so far?'  said: "You've selected your template, added your
                                         logo, and filled in your company details."
guess 'which preset did I pick?'  tools=[look_at_screen(careful)] said: "the Modern preset"
named Classic B in 0/6
```

It made history up, and it paid for careful vision calls trying to find out.

## What was built

The per-turn walkthrough context names the steps already behind them:

```
Steps already behind them (done or skipped): 1. "Click the Tools menu.";
2. "Choose Layout presets."; 3. "Pick Classic B from the list." The current
step is: "Click Apply." Tora's arrow is already on "Apply".
```

* At most the last three, each capped at 80 characters, then "(and N
  before those)". A long walkthrough costs no more than a short one.
* "Done or skipped" is literally true. GW-14's skip moves past a step
  without doing it.
* It is in the per-turn context, not the recap, because the gap is not
  specific to blank sessions. A resumed session's history does not have
  the steps either.

After:

```
KNOWS 'what have I done so far?'  "You started by clicking the Tools menu, chose
                                   Layout presets, and then picked Classic B."
KNOWS 'which preset did I pick?'  "You picked the Classic B preset."   (no vision call)
named Classic B in 6/6, restarted the walkthrough 0/6
```

The GW-14 command check, re-run on step 2 so the new line is present,
still picks the defined command 16 times in 17. The one difference is
"where is it?", which went to `point_at` on the current target: the same
arrow, and within the variance GW-14 measured.

## Proof

`tests/agents/test_e2e_guide_reconnect.py` (6):

* the steps behind ride with the question; step one has none; a long
  walkthrough names only the last three;
* the walkthrough is never folded into "same screen";
* after an `epoch` bump (a refused resume), the first question carries
  the screen in full, the goal, the current step and the steps behind;
* losing the session reconnects in the background and leaves the runner,
  its step and the plan untouched.

## Not in scope

* A same-goal guard on `show_me_how`: 0 restarts in 12 did not justify
  one. If a restart shows up in the log (`guide ready` twice for one
  goal), it is a small change.
* One run drew a box at made-up coordinates while answering from nothing.
  Drawing is GW-19.
