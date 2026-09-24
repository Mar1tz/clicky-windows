# Walkthroughs, live: the arrow shows you where as each step is said

**Written for:** the user deciding what gets fixed first, and the session
fixing it. One milestone at a time; the next starts when the last is proven.
Each lands as one commit with a doc in `milestones/LG-NN-*.md`.

**Evidence:** `tora.log` for the three walkthroughs of 24 September, read
line by line:

* 22:37 and 22:39: "add captions in CapCut";
* 23:02: "open a new tab and search for laptops on Takealot". This one ran
  *after* APP-23's arrow fix.

It was also checked against the code of every stage between "show me how"
and the arrow.

**The complaint:** "It told me the steps, but it only showed me at the end.
I need this live."

---

## What "live" means

These are the numbers every milestone below is measured against.

| moment | today | live |
|---|---|---|
| step's words on screen → arrow ON the target | 9.5–29 s, or never | **≤ 1 s** where the app has an accessibility tree; **≤ 2.5 s** where it does not (CapCut) |
| arrow stays on the target | 4 s, then it flies back to your cursor | **until the step is done** |
| you do the step → next step up | 4 s (vision check); 24 s when the conversation was busy | **≤ 1.5 s** |
| you say "next" (any wording, any language) → next step | refused: "net", "nächste" | **≤ 1 s, every time** |
| step spoken | silent: the speech model's daily limit was hit | **always**, in some voice |
| arrow on the right thing | the Windows taskbar search box, for Takealot's search bar | **the target app only** |

---

## What happens today, stage by stage

A step goes through six stages. Each one fails in the logs.

### 1. The plan: targets the planner cannot see

The planner sees one screenshot, the screen as it is when you ask, and
names every step's target from it. From step two on, it is guessing at a
screen that does not exist yet.

* **Missing targets.** "Type takealot.com into the address bar and press
  Enter" came back with no target, so it got no arrow at all.
* **Compound steps.** "Click the search bar on Takealot, type laptops, and
  press Enter" is three actions in one step. One arrow cannot show three
  things, and the step check said "not done yet" twice while you were
  halfway through.

### 2. Showing a step waits for silence, arrow included

`GuideRunner._show` waits up to 12 s for Tora to be quiet: not listening,
not thinking, not speaking. Only then does it present the step, and the
step's words and its arrow go up together. An arrow makes no sound, but it
waits for every sentence anyway.

The step check follows the same rule. At 23:03:12 the check had decided
step one was done ("a new tab has been opened"), but the next step came up
at 23:03:36, 24 s later. The live model was stuck in a tool loop, so Tora
counted as "busy" the whole time (see stage 5).

### 3. Finding the target: slow, and sometimes wrong

**CapCut, 22:39.** The accessibility tree names one control, so every
lookup went all the way down the chain. Each arrow was 16–29 s late. APP-23
fixed the order: vision now races the local lookups, and OCR reads the
right monitor, one read at a time. But the 23:02 session shows what is
left:

* **UIA gave up on "Search"** after 1.2 s: two controls scored 0.75 and
  0.70, the browser's own and the page's. It does not know the step says
  "on Takealot".
* **Vision's reply was cut off**, `"box": [964,` then `finish=MAX_TOKENS`,
  the same as `"box": [35,` at 22:39. That is two of the six vision calls
  tonight. Thinking is already off, so the model is most likely padding its
  structured reply with blank space. Six seconds lost.
* **OCR took 8.3 s and read "Q Search" at (566, 1056).** That is the
  **Windows taskbar**, not Takealot's search bar. OCR reads the whole
  monitor, taskbar included.

### 4. The arrow's flight: slow, then it leaves

The overlay flies at "teacher pace": 1.6–2.8 s per flight, 2.7–4.8 s in
slow mode. It stays on the target for 4 s, then flies back to your cursor,
because walkthrough pointing does not hold. So even a target found
instantly arrives about 2 s after its step, and it is gone before a slow
reader gets there.

### 5. "Next" is refused, and the refusal loops

The check on the live model's `guide_step` calls (GW-14) reads the text
transcript, which is worse than the model's own hearing:

* **"net"**, which was "next" transcribed badly, was refused as "a
  question".
* **"nächste"** and "dann nicht" were refused too. The check only knows
  English, and its word pattern cannot even see "ä".

The model heard "next" and called `guide_step` **41 times in 20 s**. The
realtime layer's loop limit replied "too many tool calls; just answer" 35
times and was ignored every time. The guide was frozen until the 22 s
watchdog ended the turn.

### 6. The voice: 100 lines a day, then nothing

Every step's line is spoken by the Gemini TTS model, which allows **100
requests per day**. At 22:41 the limit was hit ("retry in 3h18m"), and
from then on every step was silent. "guide step one heard 80.0s after it
was asked for" is step three's line, the first that played.

There are two more problems here:

* The error is labelled "out of Gemini credit" (APP-02). It is not: it is
  a daily limit on one model.
* The account warning flips on and off with every successful vision call.

---

## Milestones

In order of how much each makes the walkthrough feel live.

### LG-01 · The arrow never waits for a voice — S — **done** (milestones/LG-01-04-wave-one.md)

**Done:**
* **Show at once, speak when quiet.** When a step is due, its words, its
  arrow and its progress pill go up at once. Only its *speech* waits for a
  quiet moment.
* **A busy conversation delays only speech.** A step the check has seen
  done advances on screen even while the conversation is busy; its speech
  waits.
* **Nothing holds the guide for more than 3 s.** Not a stuck or looping
  turn, not the 12 s wait for silence.

*Surface:* `tutor_features/guide.py`, `companion_manager.py`.
*Proof:* with the model "speaking" for 10 s when a step comes due, the
arrow and the words are up within 0.2 s and the voice follows. Replaying
23:03:12, step two is shown within 1 s of step one being seen done, not
24 s.

### LG-02 · The arrow gets there fast and stays — S — **done** (milestones/LG-01-04-wave-one.md)

**Done:**
* **Fast flight for walkthroughs.** An arrow for a walkthrough step flies
  in 0.4 s. Pointing in Q&A keeps teacher pace. Slow mode doubles it.
* **Stays until the step ends.** The arrow holds on the target and the
  ring stays until the step ends, then moves straight on to the next
  step's target or leaves.

*Surface:* `ui/overlay.py`, `companion_manager.py`.
*Proof:* overlay tests on the flight time and the hold; a step shown for
20 s keeps its arrow on the target for all 20.

### LG-03 · "Next" always means next — S — **done** (milestones/LG-01-04-wave-one.md)

**Done:** `guide_step` trusts the model when the transcript is unreliable:
* **Near misses.** Words close to "next" ("net", "nex", "necks", "text")
  count.
* **Other languages.** "Nächste", "weiter", "siguiente", "suivant",
  "próximo", "volgende" and "nästa" count too.
* **A second ask wins.** If the model asks for the same move twice for one
  utterance after a refusal, the move is made: it heard the audio.

A real loop breaker:
* The brake still stops running tools after six rounds.
* After ten, the session is closed and the conversation resumes in a fresh
  one. It no longer answers 35 errors that the model ignores.
* The guide is never blocked by that turn (LG-01).

*Surface:* `tutor_features/guide_intent.py`, `companion_manager.py`,
`ai/gemini_realtime.py`.
*Proof:* the 23:03 transcript replayed: "net" moves once, "nächste" moves,
and the 41-call loop becomes at most 3 calls.

### LG-04 · The step is always spoken — S — **done** (milestones/LG-01-04-wave-one.md)

**Done:**
* **Windows voice fallback.** When Google refuses the TTS model (the
  daily limit, a key problem, or no key), the step is spoken in the
  Windows voice (SAPI, local, free, already reachable through comtypes)
  for as long as the refusal lasts.
* **The words and the arrow never depend on it.**
* **A daily limit is called a limit.** A 429 on the daily quota is
  reported as "the daily limit for spoken replies; back at 02:00", not as
  "out of credit".
* **One warning, not flicker.** A refusal from one model does not flip the
  account warning each time another model answers.

*Surface:* `audio/tts/`, `ai/gemini.py`, `companion_manager.py`.
*Proof:* a scripted 429 makes the Windows voice speak the step. The
account problem is set once and cleared once.

### LG-05 · The right target, in under a second — M

**Done:**
* **UIA.** Ties are broken with the step's own words ("on Takealot"
  means the page, not the browser's toolbar). The search is limited to
  the target app's window.
* **OCR.** It reads only the target app's window rectangle, never the
  taskbar or Tora's own windows. It is the last local resort.
* **Vision.** The cut-off replies are fixed (see *Cost* below). The model
  is asked for Gemini's native `box_2d` without a strict schema, and a
  reply missing its box is retried once at once.
* **Step one pointed from the plan.** The planner already sees the
  screenshot, so it returns step one's box, and step one's arrow needs no
  lookup at all.

*Surface:* `ai/hybrid_pointer.py`, `ai/gemini_vision.py`,
`ai/gemini_planner.py`, `companion_manager.py`.
*Proof:*
* **Fixtures.** Fixtures from tonight: two "Search" controls, and the
  taskbar's "Q Search" text.
* **Timing.** Step shown → arrow landed, from the log (LG-08): p50 ≤ 1 s
  in the browser, ≤ 2.5 s in CapCut.

### LG-06 · The next step's target is found before it is due — M

This is GW-17, done properly.

**Done:**
* **Look the moment the screen changes.** When the screen changes during
  step N, and the step check is asking whether it is done, step N+1's
  target is looked up against that new screen at the same time.
* **Arrow and words together.** When step N+1 is shown, its arrow is
  usually already known and lands with the words.
* **A stale match is never used.** A guess made against the wrong screen
  is checked with the same freshness rule as APP-04.

*Surface:* `companion_manager.py`.
*Proof:* scripted: the screen changes, then the step is done; the next
arrow is drawn in the same frame as the next step's words.

### LG-07 · Every step has something to point at, and one thing to do — M

**Done:**
* **Every action step names its target.** Typing steps name the field ("the
  address bar").
* **Compound steps are split.** "Click X, type Y, and press Enter" becomes
  three steps, each with one arrow and one clear finish.
* **Later steps say what they point at.** The planner marks their targets
  as expected to appear, and LG-06 resolves them when they do.

*Surface:* `ai/gemini_planner.py` (prompt and splitter).
*Proof:* tonight's two plans, re-planned from their logged screens, have
a target on every action step and no compound steps. This needs about two
planner calls.

### LG-08 · Knowing a step is done within a second — M

**Done:**
* **Local signals decide first.** Window title, URL, focus, a new tab, a
  control appearing, text typed. The vision check runs only when none can
  tell.
* **"A new tab opened" is known locally.** It took a 4 s vision call at
  23:03:08.

*Surface:* `ai/verification.py`, `tutor_features/step_watch.py`.
*Proof:* the 23:03 steps replayed against their logged window titles and
URLs; each is done within 1 s of the change.

### LG-09 · Measured, every walkthrough — S

**Done:**
* **One timeline line per step.** Step shown · arrow landed (and by which
  tier) · first word heard · first user action · step done · next step
  shown.
* **A scorecard at the end of every guide.** Arrow latency p50/p90,
  arrows missing, arrows on the wrong app, next-step latency, and silent
  steps.

This is what proves LG-01 to LG-08 in the field, and what the next
complaint gets diagnosed from.

*Surface:* `companion_manager.py`.
*Proof:* the scorecard line, from a replayed guide.

### LG-10 · A live acceptance run — S (needs your yes)

**Done:** the three walkthroughs from tonight are run live, with the
scorecard on:
* "open a new tab and search for laptops on Takealot";
* "add captions in CapCut";
* one in Windows Settings.

**Pass:** every number in the *live* table above.

---

## Build order

| wave | milestones | what you will notice |
|---|---|---|
| 1 | LG-01, LG-02, LG-03, LG-04 | The arrow comes with the step and stays. "Next" works. The step is always spoken. |
| 2 | LG-05, LG-06 | The arrow is on the right thing, and there within a second. |
| 3 | LG-07, LG-08 | Every step has an arrow and moves on the moment you do it. |
| 4 | LG-09, LG-10 | Numbers that prove it, from a real run. |

Wave 1 is local code, tested with scripted timings: no paid calls.

---

## Cost

Waves 1 and 3 need no paid calls. Two places need a few, and each waits
for a yes:

* **LG-05, the cut-off vision replies.** Twenty `locate` calls on saved
  screenshots, to confirm the cause and prove the fix: about 20 × 1.3k
  tokens in, which is under **$0.05**.
* **LG-07, re-planning tonight's two walkthroughs.** Two to four planner
  calls, about **$0.05**.
* **LG-10, the live run.** Three walkthroughs of three to five steps,
  including the live session and vision, about **$0.30–0.60**.

## What is out of scope

* GW-20 "do it for me".
* GW-21 replaying a saved walkthrough.
* Drawing (GW-19).

These come after walkthroughs work live.
