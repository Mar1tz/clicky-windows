# GW-06 — one action per step (narrowed by evidence)

**Status: done.** 10 new tests, 770 passing. Checked against six real
plans from the live planner, before and after.

## What the plan said, and why it was wrong

The walkthrough plan said *"the plans are too thin"*: twelve of fourteen
logged walkthroughs had two or three steps. It proposed pushing for more
steps and regenerating "coarse" plans with the careful model.

Reading actual plans instead of counting them said otherwise:

```
'update Visual Studio Code'  (2 steps)
   1. Click the Help menu at the top of the window.   [target: 'Help']
   2. Select Check for Updates from the menu.          [target: 'Check for Updates...']
```

That *is* the whole path. Dark mode, wallpaper, share-in-Meet and
save-as-PDF all came back as three or four correct, single-action steps
along the real click path. The short counts were short tasks, not thin
plans. Doing GW-06 as written — padding plans, and routing "coarse" ones to
a model measured at 17.6 s — would have made walkthroughs **worse**.

## The real defect

One of six plans had a compound step:

```
4. Select your picture and click Choose picture.   [target: 'Choose picture']
```

Two things to point at, two things to happen, one step. The tutor can
point at only one of them and watch for only one.

## What was built

* **The prompt names the mistake it actually makes**, with its exception:
  *"Select your picture and click Choose picture" is TWO steps; "Type
  youtube.com and press Enter" IS one step — one intent, finished by one
  keypress.*
* **`split_compound_steps`** splits what still slips through — and only the
  unambiguous shape: two clauses joined by "and" / "and then", *both*
  starting with a pointing verb (click, select, choose, open, tap, toggle…).
  The second half keeps the step's target and outcome signals (the
  planner wrote them for the end of the step); the first half gets a
  target only if it names a real on-screen control, and its finish line
  becomes the second half's target appearing. "Or" is never split — that
  is the user choosing one route.

## After

The re-run of the same six goals: no compounds from the model at all —
the wallpaper step became *"choose a recent picture, **or** click Browse
photos"*, which is correctly one step. The splitter is the safety net for
when it does.

## Proof

`tests/agents/test_e2e_guide_plan.py` (+10): two clicks become two steps;
typing-and-Enter stays one; single actions, "click X and wait", and "type A
and B" are never split; the first half finds a real control if it names
one; outcome signals stay with the half they describe; "and then" splits
the same way; the prompt says both halves of the rule.
