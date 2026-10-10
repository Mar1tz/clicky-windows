# Show, don't tell: understand what they want, and draw it - fast, and no dearer

**Written for:** whoever decides what is built first, and whoever builds it
(Claude or Dylan). One milestone at a time; each lands as one commit.

## Where this started, and what it is about

A user on chess.com asked "what's my best next move?".

* **What Tora said.** It read the board back: "…identified a piece. Let me
  recount. Black has 7 pawns (a5, c6, d7, e7, f7, g7, h7)".
* **What it drew.** Two labelled circles, "Knight on b1" and "c3 square",
  and no arrow.
* **The card.** Long, and it ran off the screen.

The owner's direction after wave 1 (5 Oct): this is **not** about Tora
playing chess well. It is about:

* **understanding** what the user wants;
* **knowing where to draw;**
* doing both **fast**;
* **without raising the API cost** of a message.

Chess was only where it showed.

---

## Done: wave 1 (`e30465f`)

* **SN-01 · Every screen answer is timed.**
  * `tora.log` gets an answer timeline: look called, look back (model and
    tokens), first drawing, first word.
  * A running *answer scorecard* sums them up.
* **SN-02 · One line, never thinking out loud.**
  * The look gives the voice a `say` line and what it drew. Its readings go
    to `tora.log`, not to the voice.
  * The rules say "say it and stop". The card shows no bullets.
  * This is fewer tokens into every live turn.
* **SN-03 · What to do is drawn as one thing.**
  * A move is an arrow: the look's schema has `move` with a `to_box`.
  * A what-to-do question (best move, what next, which one, where to
    click) gets exactly one thing drawn.
  * It was shown live: one arrow b1→c3, and the line in the card.

**Built, then parked (`f48da3d`):** a chess engine on the PC, SN-06. It is
free, legal, and answers in about 0.8 s. But it needs an exact position,
and getting one is chess-only work. See *Parked* below.

---

## The review: how a screen question becomes a drawing today

| stage | what happens | time | cost |
|---|---|---|---|
| **1. Hearing what they want** | The live model picks a tool from its rules. The only local safety net is for "where is…" (`is_locate` → `_ensure_pointed`). | – | – |
| **2. Finding a named thing** | `point_at`'s chain: the last arrow, the accessibility tree, OCR, then vision. Cheapest first. **Sound.** | ~5 ms to ~1 s | free unless vision |
| **3. Looking at pixels** | `look_at_screen`: one vision call on the **whole monitor**. | see 4 | see 4 |
| **4. Which model** | **Deep:** Pro, up to 1,536 thinking tokens. **Fast:** Flash, no thinking. | measured: deep 4–7 s; fast 2–3 s | measured: deep **≈ $0.008**; fast **≈ $0.0014** (HeyTora's own price table: Pro $2.5/$15, Flash $0.5/$3 per million). The estimate before measuring was $0.026 and 8–20 s for deep. |
| **5. Where it draws** | The vision model's box, as it came. Snapping exists only for geometry figures. | – | – |
| **6. Saying it** | Fixed in wave 1. | – | – |
| **7. Asking again** | A look is never reused. "Show me again" on the same screen pays again. | full | full |

### The findings, in order of what they cost the user

#### 1. Most looks go to the expensive, slow model

* **The question's wording sends it there.** `wants_deep()` sends anything
  with words like *best, which, how do I, why, explain, check, fix, chart,
  error, code* to the deep model. Measured on 20 everyday screen
  questions, **11 of 20** went deep on their words alone.
* **The voice sends it there too.** Its rules say "pass careful:true when
  it needs real reading or judgement", which forces deep as well.
* **The cost.** Most looks probably pay about **23× the fast price** and
  wait **5–10× longer**. This is the biggest lever on both speed and cost.
  The new timeline line ("deep"/"fast") will confirm the share.

#### 2. It looks at the whole monitor and ignores the strongest hint

* **The whole monitor.** The look gets the full screen at 1,024–1,280 px
  wide. The window the user is working in may be a fraction of it, so the
  thing asked about is small and costs as many tokens as the rest.
* **No mouse position.** Where the mouse is never reaches the look. It is
  the best free clue there is for "this", "here" and "which of these
  should I pick". People hover over what they mean.

#### 3. A drawing lands where the model guessed, not on the real thing

* A look's ring around a button sits wherever the box came out, sometimes
  half off.
* Windows knows that button's exact rectangle and will say so in about
  5 ms. That is never asked.

#### 4. Nothing catches a what-to-do answer with no drawing

* **"Where is X":** if the voice answers in words, the safety net still
  points.
* **"What should I do / which one":** if the voice answers in words, the
  user gets words.

#### 5. Asking again pays again

* Pointing remembers the last arrow, so "that" works.
* A look remembers nothing. "Show me again" and "where was it?" on an
  unchanged screen make the same paid call twice.

---

## Next: the general path (UD-01 to UD-06)

### UD-01 · Fast first; deep only when needed — M — **done, measured**

**The comparison** (5 Oct, $0.10): 16 everyday screens with known answers,
built as HTML and rendered.

| screen | what was asked |
|---|---|
| sign-in page | where to click |
| settings | the switch |
| products | the cheapest with 4+ stars |
| chart | the highest month |
| spreadsheet | the wrong total |
| error dialog | what to click |
| inbox | the boss's email |
| checkout | how to pay |
| video player | the unlabelled subtitles icon |
| form | the missing field |
| file list | the biggest file |
| tic-tac-toe | where to play |
| worksheet | the wrong sum |
| menu | the cheapest vegetarian dish |
| payment form | where the card number goes |
| chess | a mate in one |

**What the fast model did:**
* It drew the right thing on **15 of 16**, at a median of 2.6 s and
  ~$0.0014 a look.
* **Its one miss was the chess move, made with confidence.** It said
  "you are checkmated" and marked itself `sure`. The deep model played
  Ra8#.
* So a move in a game (`needs_judgement`) goes deep first, like dense
  reading. Everything else starts fast.

**Measured costs:**

| | cost per look | time |
|---|---|---|
| fast | $0.0014 | 2–3 s |
| deep | $0.008 | 4–7 s |

Deep is about **6×** the fast cost, not the 23× estimated before
measuring.

**What this saves:**
* The saving depends on how often looks went deep before.
* Deep was picked by the question's wording (11 of 20 everyday questions)
  and by the voice's `careful` flag, which no longer forces it.
* The live scorecard's fast/deep share shows it.

**Done:**

* **Every look starts on the fast model,** thinking off, with the action
  schema.
* **The schema adds `sure`:** "could you read what was asked and answer
  it?".
* **Deep runs only when:**
  * the fast look is not sure; or
  * a what-to-do question came back with nothing to draw; or
  * it is dense reading: code, small print, a full sheet.

  It runs **once**, never both by default.
* **`careful` from the voice** no longer forces deep by itself.

*Proof:*
* About 20 saved screenshots of everyday questions. Fast and deep agree on
  what to draw in ≥ 85% of cases, and the rest escalate.
* Average cost per look ≤ ⅕ of today's.
* p50 time to the drawing ≤ 2.5 s.

*Cost:* the comparison is about 20 fast and 10 deep calls, **≈ $0.30**. It
waits for a yes.

### UD-02 · Look where they are looking — S — free — **done** (offline tests; the crop's effect on accuracy shows in SN-10)

**Done:**

* **The window in front, cropped.** The look gets that window at the same
  pixel budget: sharper for the same tokens, or fewer tokens when the
  window is small.
* **The mouse position** goes in as a hint: "the user's pointer is at
  (x, y)".
* **Boxes map back** from the crop to the screen, on any monitor and
  scaling.

*Proof:* a cropped look's box lands on the right screen pixels across
mixed-scale monitors, and the hint is in the prompt.

### UD-03 · Land on the real thing — S — free — **done; checked live**

Checked live on this PC: a box drawn a little off the taskbar's Start
button snapped exactly onto the button's rectangle, in 5 ms.

**Done:**

* **Snap to the real control.** A look's box over a UI control is snapped
  to that control's exact rectangle: the accessibility tree's element at
  the box centre, kept only when its size is close to the box.
* **Rings and arrows then sit exactly** on the button, cell or link.
* **Pictures and games are left alone:** there is no control there.

*Proof:* a recorded look whose box is 20 px off a button rings the
button's own rectangle.

### UD-04 · No answer without its drawing — S — no dearer than a correct turn — **done**

The target comes from the answer's own words: "Click Checkout to pay" →
"Checkout", "Pick the Arctic Fan for $39" → "Arctic Fan". An answer that
names nothing on screen ("maybe go for a walk") draws nothing.

**Done:**

* **When it applies:** a where-is or what-to-do turn that ended with
  nothing drawn.
* **How it is finished:** with the point chain first (free tiers), using
  the answer's own words; else with **one fast look**.
* **The cost:** that look is the one the voice should have made, so it
  costs nothing over a correct turn.

*Proof:* a scripted "what should I click next?" answered only in words
still ends with an arrow.

### UD-05 · Don't pay twice — S — saves — **done**

The screen's sketch is a 16×9 average hash. It survives JPEG noise and
Tora's own arrow, and changes with the screen. Reuse lasts three minutes,
and applies to the same question or to "again", "where was it" and
"remind me".

**Done:**

* **The last look is kept,** with a small fingerprint of the window it
  saw.
* **On an unchanged screen,** a follow-up ("show me again", "where was
  it?", "and why?") is answered from it: the drawing is redrawn, with no
  call.
* **A changed screen** gets a new look.

*Proof:* a second identical question on an unchanged screen makes 0 vision
calls.

### UD-06 · Cost per answer, measured — S — **done**

Each answer's timeline line now ends with its look's cost, e.g.
"$0.0014". The scorecard gets the average, and how often each path ran:
`look cost avg $0.0036 (fast 1, fast→deep 1, reused 1)`. This is the cost
of the screen look; the live voice turn itself is not included.

**Done:**

* **The answer scorecard adds:**
  * the estimated cost per answer, from the tokens and the same price
    table the server bills with;
  * the share of fast and deep looks.
* Each release can then show it did not get dearer.

*Proof:* the scorecard line carries cost per answer, and the live run
compares it with wave 1's.

### SN-09 · The card stays on screen, and off the answer — S — **done; shown live**

**Shown live:** with the board low enough that the card's usual place
covered the knight, the card slid to the screen's left edge the moment
the arrow was drawn, clear of the board. It goes back by the pill once
the drawing is cleared.

**The clipped card:** the card is now placed on the screen the pill is
actually on, and checked again on the screen it lands on. The two-monitor
case itself was not reproduced on this PC.

* **The clipped card in the second photo** is reproduced (two monitors,
  mixed scaling, docked right) and fixed.
* **The card never covers what Tora drew.** In the wave-1 demo it covered
  the board's first rank: the knight the arrow starts from. While a
  drawing is up, the card moves to the side of the screen away from it.

### SN-10 · A live acceptance run, across apps — S — **stopped at the owner's word ("this all works")**

**What was tried (5 Oct):** the 16 screens as real Edge windows, with the
real look path on each.

**Why it is invalid:** a background script may not take the focus, so
every look saw the window in front instead: the editor. It sent about 16
screenshots of it through the owner's own key, $0.058. 0/16 was
therefore not a result.

**The harness fix:** the window made topmost, and checked to be the one
on screen before any call. It was written, then the owner stopped the
pop-ups.

**What stands in for it:**
* the measured comparison (UD-01, 15/16 fast, the miss now routed deep);
* the live snapping check (UD-03);
* the live card demo (SN-09).

The voice side (one line, the look called) is to be checked in normal
use, from the answer timeline and scorecard in `tora.log`.

Twenty questions, each with the kind of answer it should get:

| where | question | expected |
|---|---|---|
| Excel | "which cell is wrong?" | ring |
| a web form | "where do I click next?" | arrow |
| Windows Settings | "how do I turn on dark mode?" | walkthrough |
| a chart | "which month was highest?" | ring and one line |
| a chess game | "best move?" | one arrow |
| and others | "which one should I pick?" | one thing drawn |

**Pass:**
* ≥ 18/20 drawn on the right thing;
* one line each;
* p50 time to the drawing ≤ 2.5 s;
* average cost per answer **≤ wave 1's**, with a target of ≤ ⅓.

*Cost:* **≈ $0.20**.

---

## Parked: exact chess

These help chess alone. They are not planned unless chess becomes a
priority.

* **SN-06, the engine:** built (`f48da3d`). It is legal, answers in about
  0.8 s, says why, and is free. It is only useful with an exact position.
* **SN-05, reading the move list:** the spike (5 Oct) found lichess's move
  list is **not** in the accessibility tree (279 elements, no moves).
  Reading it would need OCR of the move-list panel, or a browser hook.
* **SN-04, board geometry: done (10 Oct).** A user was told "knight from
  g1 to f3" four times and shown g1 → g3 every time: the model's words
  were right and its box was a file off. `ai/look_board.py` finds the 8×8
  board in the look's own screenshot (the even lattice of square borders,
  checked as a checker of two colours) and draws a move between the
  squares the answer names. The model's boxes only say which way up the
  board is. It runs in about 0.1 s, with no call. On the user's own
  screenshot it found the board to a pixel. In tests it held for chess.com
  and lichess colours, black at the bottom, highlights, and the card over
  a corner; it found no board on any of the bench's 22 other screens.
  Boards with squares under about 20 px are left to the model's boxes.
  Tests: `tests/agents/test_e2e_move_on_its_square.py`.
* **SN-07:** the position from pixels.

Without them, a chess question takes the general path: UD-01's look,
escalating to deep when the fast model is not sure. That is the right
model for hard judgement.

---

## Build order

| wave | milestones | what you will notice |
|---|---|---|
| 1 — done | SN-01, SN-02, SN-03 | One thing drawn and one line instead of a lecture; every answer timed |
| 2 | UD-01, UD-02, UD-03 | Most answers in ~2 s instead of 10–20, for a fraction of the cost, drawn exactly on the real thing |
| 3 | UD-04, UD-05, UD-06 | Never just words; never paid twice; the cost of each answer on the scorecard |
| 4 | SN-09, SN-10 | The card stays clear of the drawing; a live run proves it across apps |

## Cost

**Per answer:**

| milestone | effect |
|---|---|
| UD-01 | Every look that used to go deep and need not (by its words or the `careful` flag) goes from **~$0.008 to ~$0.0014**, and is 2–4 s faster (measured). |
| UD-02 | The same pixels or fewer. |
| UD-03, UD-05 | Free; UD-05 saves whole calls. |
| UD-04 | Only the look a correct turn would have made. |

**To build:**

| what | about |
|---|---|
| UD-01's comparison | $0.30 |
| SN-10's live run | $0.20 |

Each waits for a yes.

## Decision needed

* **UD-01's comparison run (≈ $0.30).** It proves the fast model is good
  enough before it becomes the default.
* Without it, UD-01 can still ship with deep as the fallback when the
  fast look is not sure. That is safe, but unmeasured.
