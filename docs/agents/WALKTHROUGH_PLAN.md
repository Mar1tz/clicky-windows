# Walkthroughs: from "it sucks" to something people rely on

**Status:** waves 1–4 are built and proven — GW-01 to GW-07 and GW-09 to
GW-16 — each recorded in `milestones/GW-NN-*.md`. GW-06 was narrowed after
live plans showed its premise was wrong, and GW-15's premise was measured
and narrowed the same way (see their docs). GW-16 saves a finished
walkthrough instead of offering to. Waves 5–7 remain.
One milestone, one commit, and the next does not start until the last is
proven.

**Written for:** the user choosing what to build first, and the session
that builds it.

**Evidence:** every walkthrough in `tora.log` — 14 of them, 21–23 September
2026 — plus a read of the whole pipeline. Numbers below are from the log,
not estimates.

---

## 1. What actually happens today

### The worst session, second by second

"Update Visual Studio Code", 23 September, 16:39:

```
16:39:08  show_me_how
16:39:12  guide ready: 2 steps                    ← 4 s planning; 2 steps for a real task
16:39:15  pointed at 'Help' (menu item)            ← good
16:39:17  watching control_appears                 ← the arming fix works
16:39:23  step done — click on target              ← detected the click. good.
16:39:42  the user says something (1.8 s)
16:39:44  guide_step
16:39:45  guide_step   (ignored as a repeat)
16:39:47  guide_step
   …       39 calls in 24 seconds, one every ~0.6 s …
16:40:08  guide_step → error: no guide is running  ← the model walked off the end
```

The first half of that is the walkthrough working as designed. The second
half is the live model calling `guide_step` in a tight loop, on its own
results, with nothing to stop it.

### Why nothing stopped it — and it is my regression

`MAX_TOOL_ROUNDS = 6` exists precisely to stop a tool chain that never
settles. In the whole log it has fired **zero times**.

Field fix 5 (23 September) reset the tool budget in two places: when a new
question starts, and on every `turn_complete`. The first was the fix. The
second was a mistake: on the Live API **every tool round ends with its own
`turn_complete`**, so the counter goes 1 → 0 → 1 → 0 and can never reach 6.
The test that was meant to guard it scripted nine tool calls *without*
`turn_complete`s between them, which is not what the wire does, so it
passed.

The repeat guard added the day before (identical command within 1.5 s is
ignored) caught 22 of the 39 calls. The other 17 were more than 1.5 s
apart and went through. A guard tuned to the model's retry cadence was
never going to hold against a model in a loop.

### Across all fourteen walkthroughs

| measure | value |
|---|---|
| walkthroughs | 14 |
| steps per plan | 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 4, 6 |
| planning time (where measurable) | 3.6 – 4.4 s |
| targets the local pointer could not find → vision model | **52** |
| UIA matches too ambiguous to trust | 15 |
| times the runaway brake fired | **0** |
| times a walkthrough's *end* was logged | **0** |
| `draw` calls that failed to parse | 2 |
| steps that finished only after vision said "not done yet" first | 5 |

Three things jump out.

~~**The plans are too thin.**~~ **Wrong — corrected by GW-06.** This was
read from step *counts*. Reading the step *text* of live plans showed they
follow the real click path: "update Visual Studio Code" really is Help →
Check for Updates... Short counts were short tasks. The actual defect was
the occasional compound step ("select your picture *and* click Choose
picture"), which GW-06 fixes.

**The pointer misses constantly.** 52 fallbacks to the vision model, each
costing two to four seconds of silence and a paid call. The planner names
targets by *guessing from a screenshot*; the pointer then *searches* for
that guess; when the guess is not exactly what the app calls it, it misses.

**The walkthrough is invisible.** Nothing logs a guide finishing, stopping,
getting stuck or being nudged. The durations in the audit (six to ten
minutes for three steps) are meaningless because there is no end marker to
measure to. Every problem report about walkthroughs is currently guesswork.

### The CapCut session, which shows the rest

```
23:31:28  guide ready: 3 steps for 'Generate auto captions in CapCut'
23:31:35  pointer missed 'English' — vision model next
23:31:50  the user had to say "next"                ← step 1 never auto-detected
23:31:59  pointer missed 'Generate' — vision model next
23:32:06  step done — click on target
23:32:17  not done yet (captions processing, 45%)    ← step 3 is WAITING for a job
23:32:31  step done — vision: processing finished
23:32:53  draw → error: none of those tags parsed
```

Step 3 was not an action at all. It was "wait for the captions to
generate" — and the watcher treated it like a click, asking the vision
model every time the screen changed whether it was done yet. Waiting is a
different kind of step and needs to be treated as one.

---

## 2. The pipeline, and where each stage fails

```
 ┌─ ASK ─────────── "show me how to update VS Code"
 │   live model → show_me_how(goal)                 returns instantly ✓
 │
 ├─ PLAN ─────────── gemini_planner.plan()           3.6–4.4 s
 │   screenshot + window title → JSON steps
 │   ✗ plans are 2–3 steps; real tasks are longer
 │   ✗ targets are guesses from pixels, not the app's real control names
 │   ✗ completion signals still sometimes true before the step starts
 │
 ├─ PRESENT ──────── _present_step(i)
 │   speak "Step i of n: …" + point at target
 │   ✗ 52 targets missed locally → vision → 2–4 s each
 │
 ├─ WATCH ────────── StepWatcher.poll()
 │   click inside target → expectation signal → screen change + vision judge
 │   ✗ a "wait for this to finish" step is judged like a click
 │   ✗ typing is only detectable by vision
 │
 ├─ TALK ─────────── the user speaks mid-guide
 │   ✗ any utterance can become guide_step(next)
 │   ✗ the model can call guide_step in a loop; nothing stops it
 │   ✗ a reconnect (frequent) starts a session that half-knows a guide runs
 │
 └─ END ──────────── completed / stopped / abandoned
     ✗ none of it is logged; no summary; nothing learned
```

---

## 3. Milestones

Format for each: **symptom** (what the user or the log shows), **done**
(what "fixed" means), **surface**, **size** (S ≈ a sitting · M ≈ a day ·
L ≈ several), and **proof** (the test that shows it). A milestone whose
proof cannot be written as a test is not ready to build.

### Phase 1 — stop it breaking

**GW-01 · The runaway brake works again** — S
*Symptom:* 39 `guide_step` calls in 24 s; `MAX_TOOL_ROUNDS` fired 0 times.
*Done:* the tool budget resets when a new user request starts, and only
then — not on `turn_complete`. Field fix 5's actual goal (a long
conversation must not use up the budget) is kept.
*Surface:* `ai/gemini_realtime.py`.
*Proof:* a scripted session in the real wire shape — `tool_call`,
`turn_complete`, `tool_call`, `turn_complete`, ×10 — is capped at six;
the existing "long conversation" test still passes.

**GW-02 · One "next" is one step, whatever the model does** — S
*Symptom:* the 1.5 s repeat guard let 17 of 39 calls through.
*Done:* after a manual advance, further `next`s are refused until the new
step has been *spoken* — tied to the guide's state, not a timer. The tool
result says, in words the model cannot misread, "moved to step 2 of 2 —
stop calling guide_step and speak".
*Surface:* `tutor_features/guide.py`, `companion_manager.py`.
*Proof:* 40 `next`s fired 0.6 s apart advance exactly one step.

**GW-03 · The walkthrough's life is in the log** — S
*Symptom:* 0 walkthrough endings logged in 14 walkthroughs.
*Done:* one line each for: started (goal, step count, planning ms), step
shown (index, how the target was resolved, ms), step done (how, ms after
shown), stuck, nudged, and finished/stopped/abandoned (why) — plus a
one-line summary per guide: steps, auto-completed, manual nexts, vision
calls, total time.
*Surface:* `companion_manager.py`, `tutor_features/guide.py`.
*Proof:* a scripted guide produces exactly the expected lifecycle lines.

**GW-04 · Tora never hears herself say "next"** — S
*Symptom:* unverified — but a step that *contains* the word "next"
("click Next") spoken while the mic is open would be heard as a command.
*Done:* proven that step speech cannot reach the model as user input, or
fixed so it cannot.
*Surface:* `audio/`, `companion_manager.py`.
*Proof:* a test that plays a step containing "next" and asserts no
`guide_step` results.

### Phase 2 — plans a person could actually follow

**GW-05 · Plan against the app's real controls** — M
*The single biggest lever in this document.*
*Symptom:* 52 pointer misses. The planner names targets by guessing from a
screenshot; the app calls them something else.
*Done:* UC-04 now reads the real controls of any window in 0.2–0.4 s.
The planner receives that list and must use those exact names as targets.
A target that is not in the list is either justified (it appears after an
earlier step) or rejected.
*Surface:* `ai/gemini_planner.py`, `companion_manager.py`,
`agents/control/providers/windows_ui.py`.
*Proof:* on a recorded Notepad/Calculator screen, every step-1 target is a
name UC-04 returned; pointer misses on step 1 drop to zero.

**GW-06 · Plans with every step in them** — M
*Symptom:* 12 of 14 plans were 2–3 steps.
*Done:* the planner is told the granularity rule with examples (one click,
one field, one menu per step), plans are validated (a step with two verbs
is split or rejected), and a plan that looks too coarse for its goal is
regenerated by the careful model.
*Surface:* `ai/gemini_planner.py`.
*Proof:* a fixed set of ten goals produces plans whose step counts match a
hand-checked reference within one step.

**GW-07 · Stale signals are removed before step one is spoken** — S
*Symptom:* "every expectation signal (control_gone) was already true".
*Done:* each step's signals are checked against the live screen at plan
time; ones already true are dropped or rewritten, and a step with nothing
left gets the next step's target as its finish line (Field fix 6).
*Surface:* `companion_manager.py`, `ai/verification.py`.
*Proof:* a plan with an already-true signal is repaired before presentation.

**GW-08 · Step one arrives faster** — M
*Symptom:* 3.6–4.4 s from "show me how" to step one.
*Done:* step one is presented as soon as it exists — streamed from the
plan, or planned separately and fast — while the rest of the plan
finishes behind it.
*Surface:* `ai/gemini_planner.py`, `companion_manager.py`.
*Proof:* measured time to first step, logged by GW-03, under 2.5 s median.

### Phase 3 — knowing when a step is done

**GW-09 · "Wait for it" is its own kind of step** — M
*Symptom:* CapCut's "captions are processing" step was judged like a click
by repeated vision calls.
*Done:* the planner marks waiting steps; the watcher polls them cheaply
(window text via `read_text`, progress bars, a thumbnail) and asks vision
only at the end; Tora says "this takes a moment — I'll tell you when it's
done", and does.
*Surface:* `ai/gemini_planner.py`, `tutor_features/step_watch.py`.
*Proof:* a scripted progress sequence completes with zero vision calls.

**GW-10 · Typing is detected without looking** — S
*Done:* a step that asks for text is complete when the focused field's
UIA value changes to something non-empty — a local read, not a screenshot.
*Surface:* `tutor_features/step_watch.py`.
*Proof:* a scripted value change completes the step with no vision call.

**GW-11 · The cheapest signal wins, and we know which one did** — M
*Done:* completion is checked in order — click on target, UIA signal,
window text change, OCR, then vision — and GW-03 records which fired.
*Target:* the vision judge decides under 20 % of steps.
*Surface:* `tutor_features/step_watch.py`.
*Proof:* the GW-03 summary over a scripted guide set.

**GW-12 · When unsure, ask instead of guessing** — S
*Done:* after two "not done yet" judgements on a screen that has clearly
changed, Tora asks "looks like you've done that — shall I move on?"
rather than waiting in silence or advancing on a guess.
*Surface:* `tutor_features/step_watch.py`, `companion_manager.py`.
*Proof:* scripted ambiguous screens produce one question, not a stall.

### Phase 4 — the conversation during a walkthrough

**GW-13 · A question is not a "next"** — M
*Symptom:* any utterance mid-guide can become `guide_step(next)`.
*Done:* the per-turn guide context lists exactly which phrases are
commands and says that everything else is a question to answer *without*
touching the guide. "Where is it?" re-points. "What does this do?" is
answered. "Done" / "got it" advances.
*Surface:* `companion_manager.py`, `ai/gemini_realtime.py`.
*Proof:* twenty scripted mid-guide utterances, each producing the right
action or none.

**GW-14 · Every command has a defined, tested meaning** — S
next · back · repeat · skip · stop · pause · resume · "where is it" ·
"I'm stuck" · "do it for me" — each with one behaviour, each with a test.

**GW-15 · A walkthrough survives a reconnect** — M
*Symptom:* "could not resume the conversation" appears dozens of times a
day; ~~a fresh session only half-knows a guide is running~~. **Measured
by GW-15:** 15 in 112 opens. A blank session knew the goal, the step and
the count, and restarted nothing (0 in 12). What no session knew, blank
or resumed, was the steps already behind the user.
*Done:* on a blank session the guide's state — goal, current step, what
was already done — is re-announced, and the guide carries on unbroken.
*Surface:* `companion_manager.py`.
*Proof:* a scripted reconnect mid-guide, then "what step am I on?" is
answered correctly.

**GW-16 · Finishing is an event** — S
*Done:* the last step says "that's it, done", shows a one-line summary,
and ~~offers to save~~ **saves** the walkthrough to the journal so it can be
replayed. The journal already keeps every answer unasked, and an offer
spoken in Tora's local voice is one the live model never hears (see the
GW-16 doc).
*Surface:* `companion_manager.py`, `tutor_features/journal.py`.

### Phase 5 — pointing

**GW-17 · Targets are resolved before they are needed** — M
*Done:* each step's target is pre-resolved against the UC-04 control list
as soon as that step's screen is up — not when the step is spoken — and
cached per window, so the arrow lands with the first word.
*Surface:* `companion_manager.py`.
*Proof:* median time from step spoken to arrow landed, from GW-03.

**GW-18 · Fewer trips to the vision model** — M
*Done:* the ladder is UC-04 names → UIA walk → OCR (fixed in Field fix 6)
→ vision; each tier's hit rate is logged; vision fallbacks per guide drop
by at least three quarters against the 52 measured.
*Surface:* `ai/hybrid_pointer.py`, `companion_manager.py`.

**GW-19 · Drawing works** — S
*Symptom:* `draw → none of those tags parsed`.
*Done:* the parser accepts the forms the model actually produces, and a
failure tells the model the exact syntax to use.
*Surface:* `companion_manager.py`.

### Phase 6 — the one that makes it remarkable

**GW-20 · "Just do it for me"** — L
UC-04 means Tora can press most of the controls it points at. So a
walkthrough can become an errand: at any step, "do it for me" performs
that step through the same confirmed, risk-checked action path as any
desktop action — and "do the rest" runs the remaining steps, pausing at
anything SENSITIVE.
The difference between a tutor and an assistant, in one sentence.
*Surface:* `companion_manager.py`, `agents/control/`.
*Proof:* the Calculator "5 × 5" guide completes end to end on "do it for
me", and a guide with a *Delete* step stops and asks.

**GW-21 · Learn from every walkthrough** — M
*Done:* a finished guide is saved with the real targets that worked; the
next request for the same goal in the same app replays the saved plan
instead of planning from scratch — instant, and already proven.
*Surface:* `tutor_features/`, `ai/gemini_planner.py`.

### Phase 7 — proving it stays good

**GW-22 · A replayable recording of every walkthrough** — M
Enough captured (plan, screens' control lists, signals, events) that a
field failure can be replayed in a test — the walkthrough equivalent of
DC-04.

**GW-23 · A real end-to-end walkthrough test** — M
Against Calculator and Notepad, which are safe and reversible: plan, point,
detect each step, finish — asserted, not eyeballed. Opt-in, never run
unasked, closes only what it opened.

**GW-24 · A scorecard** — S
From GW-03's summary lines: steps auto-completed, manual nexts, vision
calls, time to first step, time per step. Shown in the app window's
Tutor page. The number that says whether a change made it better.

---

## 4. Build order

| wave | milestones | what changes for the person using it |
|---|---|---|
| **1** ✅ | GW-01, GW-02, GW-03, GW-04 | It stops racing to the end on its own. Every problem becomes visible. |
| **2** ✅ | GW-05, GW-07, GW-06 | Steps point at things that exist, one action each. |
| **3** ✅ | GW-09, GW-10, GW-11, GW-12 | Steps finish when you finish them, without "say next". |
| **4** ✅ | GW-13, GW-14, GW-15, GW-16 | You can talk to it mid-walkthrough without derailing it. |
| **5** | GW-08, GW-17, GW-18, GW-19 | It feels instant: arrow with the first word, no pauses. |
| **6** | GW-20, GW-21 | "Do it for me." And the second time is instant. |
| **7** | GW-22, GW-23, GW-24 | It stays fixed, and there is a number that proves it. |

Wave 1 is small — a day, perhaps less — and it is not optional: until
GW-01 is in, any walkthrough can turn into the 16:39 session, and until
GW-03 is in, nothing after it can be measured.

GW-05 is the one to watch. It connects the walkthrough to the discovered
controls from UC-04, and if it works as expected it removes most of the
pointer misses, most of the vision calls and most of the "that's not what
it's called" moments in one go.

---

## 5. What "loads better" means, as numbers

Measured today → target after wave 5, both from GW-03's log lines:

| | today | target |
|---|---|---|
| runaway `guide_step` loops | happened, uncapped | impossible (capped at 6) |
| steps per plan for a real task | median 3 | matches the real path |
| time to step one | ~4 s | < 2.5 s |
| pointer misses → vision, per guide | ~4 | < 1 |
| steps needing a manual "next" | common | rare, and logged |
| walkthrough endings logged | 0 % | 100 % |
