# APP-04 — pointing never uses an old screen

**Status: done.** 5 new tests, 1026 passing. This is the first half of
GW-17.

## The problem

The pointer's first tier is the control snapshot taken when you last spoke.
It costs nothing and it is exactly right for "where is X?" in the same
turn. But it had no age and no window attached to it. A walkthrough step
presented a minute and three screens later was resolved against it too, so
a control sharing a name with one from back then ("Save" in a dialog, and
in the main window behind it) got the arrow where the **old** one was.
The "the one to its left" lookup used the same stale list. No test covered
this tier at all.

## What was built

The snapshot now records when and in which window it was taken. It answers
only while it is fresh:

* within 10 s (`SNAPSHOT_TRUST_S`), which is the same turn;
* in the same foreground window;
* and never after a walkthrough step has ended, because the screen has just
  been acted on.

Otherwise the live UI Automation / OCR search runs as the second tier
always did, and "the one to its left" walks the screen as it is now.

## Proof

`tests/agents/test_e2e_fresh_snapshot.py` (5):

* a fresh snapshot answers at once;
* an old one is ignored;
* one from another window is ignored;
* one taken before a walkthrough step ended is spent;
* a manager that never took one has none.
