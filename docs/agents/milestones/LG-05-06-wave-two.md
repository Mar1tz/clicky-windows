# LG-05 and LG-06 — wave two: the right target, and ready before the step

**Status: done**, except two parts that wait for a paid check (below).
Plan: `docs/agents/LIVE_GUIDE_PLAN.md`. 17 new tests, 1215 passing. Seven
mutations, each caught by the test written for it.

## LG-05 · The right target, in under a second

The three failures of 24 Sep, 23:04, on step three, "Click the search bar
on Takealot, type laptops, and press Enter":

* **UIA: "Search" was a coin flip.** The page's search box scored 0.75
  and the browser's 0.70, so UIA gave up.
  * Now a step's own instruction goes down to every tier.
  * UIA gives a control +0.15 when the names of what it sits *inside*
    carry the step's words. Inside the document titled
    "Takealot.com: …", that decides it.
  * The window's own title does not count: it names the page, so every
    control would share it.
  * Neither does the control's own name: "press Enter" must not favour a
    box called "Search or enter address".
* **OCR matched the Windows taskbar.** It found "Q Search" at (566, 1056).
  * Now OCR reads only the target app's window, clipped to its monitor,
    with hits offset by the window's position.
  * A maximised window stops at the taskbar.
* **Vision was cut off.** The reply ended at `"box": [964,` with
  finish=MAX_TOKENS, twice tonight.
  * The box is now exactly four numbers (`minItems`/`maxItems`).
  * There is room to answer (2048 tokens, was 1024).
  * Vision is told which step it is looking for.

**Deferred, needs a yes (under $0.05 each):**
* **Confirming the vision fix.** The logged reply ended at the box with
  under 80 characters written, which points to hidden deliberation using
  up the 1024 tokens. `think=0` may not be honoured by this Gemini 3
  model. Twenty `locate` calls on saved screenshots would show which, and
  prove the fix.
* **Step one's box from the plan.** It changes the planner's reply
  format, and needs two to four planner calls to prove.

## LG-06 · The next step's target is found before it is due

A step's target is usually put on screen by the step before it: clicking
"Captions" is what shows "Auto captions".

* **Look ahead as soon as the screen settles.** The step watcher now
  records when the screen last changed (`changed_at`). While step N is
  being polled, step N+1's target is looked up once the screen has settled
  for 0.6 s after a change.
  * Free tiers only: UIA and OCR. A look-ahead never makes a paid vision
    call.
* **Instant when fresh.** When step N+1 goes up, a look-ahead from the
  last 3 s, in the same window, is its arrow at once, with no lookup at
  all. A stale one, or one from another window, is looked up again against
  the screen as it is.
* **Logged as such.** The log line reads "found via ahead uia/ocr", so
  LG-09 can count how often the arrow was ready before its step.

## Proof

* `tests/agents/test_e2e_guide_targets.py` (7): the 24 Sep "Search" tie as
  a UIA tree; the window title and a control's own name not counting; the
  vision box schema; the step's words reaching vision and the local tiers.
* `tests/agents/test_e2e_guide_ahead.py` (7): once per settled change;
  free; fresh is instant; stale and wrong-window are not trusted; a real
  StepWatcher records the change.
* `tests/agents/test_e2e_guide_arrows_on_time.py` (+2): OCR reads only the
  app's window, and never the taskbar.

Seven mutations, each caught:
* context ignored;
* the window title counted;
* OCR reading the whole monitor;
* an unbounded box;
* a look-ahead that may pay;
* any look-ahead trusted;
* a watcher that never says.
