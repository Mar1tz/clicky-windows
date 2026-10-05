# Show the next move: Tora answers "what should I do?" by drawing it

**Written for:** whoever decides what is built first, and the session (or
Dylan) building it. One milestone at a time; the next starts when the last is
proven. Each lands as one commit.

**The complaint:** a user playing on chess.com asked "what's my best next
move?". Tora described the board instead of showing the move.

**Evidence:**

* **Two photos of that user's screen.**
  * The card read: "…identified a piece. Let me recount. • Black has 7
    pawns (a5, c6, d7, e7, f7, g7, h7). Lost…".
  * The board had two labelled circles, "Knight on b1" and "c3 square". There
    was no arrow.
  * A second card began "A solid next move is … which develops another … e4
    pawn". The move was never stated, and the card ran past the edge of the
    screen.
* **The code.** Every stage between the question and the drawing was read:
  * `ai/gemini_realtime.py`, `config.py` (`_REALTIME_RULES`);
  * `companion_manager.py` (`_tool_look_at_screen`);
  * `ai/gemini_vision.py`;
  * `ui/overlay.py`, `ui/flow_bar.py`.
* **No log from that PC.** The owner's `tora.log` has no chess session. So
  the "today" numbers below come from the code, and SN-01 measures them.

---

## What "done" means

| moment | today (from the code) | done |
|---|---|---|
| question ends → the move is on the board | 10–25 s: the deep vision model, up to 1,536 thinking tokens, 35 s timeout | **≤ 2 s** when the site's move list can be read; **≤ 5 s** when the board has to be read from pixels |
| what is drawn | up to 4 labelled circles; a move cannot be an arrow at all | **one arrow**, piece → square, nothing else on the board |
| what is said | the look's answer plus its piece-by-piece readings, sometimes recounted aloud | **one line**: "Knight to c3: it develops and guards e4" |
| where the move comes from | a vision model's opinion of a whole-screen screenshot | a **chess engine on the PC**, from an **exact** position, always legal |
| a follow-up ("and then?") | the whole board read again | **no new look**: the position is kept |
| board it cannot read | guesses, recounts aloud | **one line**: "I can't read this board clearly - can you show the move list?" |
| cost per "best move" | one deep vision call (~1.3k image tokens in, up to ~1.8k out: roughly $0.02–0.03 at Pro list prices), plus the live model reading ~300 tokens of readings | **$0 of vision** when the move list can be read; otherwise **one fast call on the board alone**, thinking off. **Never more than today.** |

---

## Why it fails today

### 1. A move cannot be an arrow

`ai/gemini_vision.py` is the only pipe from "the model saw it" to "the user
sees it", and it carries one box per item:

* `Action` holds one box.
* `Action.as_tag()` turns every kind except `rect` and `underline` into a
  labelled `CIRCLE`.
* The schema has no "from → to".

So "knight from b1 to c3" can only ever reach the screen as two labelled
circles. That is exactly the photo. The voice model could draw an arrow
between the two highlights itself, but nothing asks it to.

### 2. Tora's working is handed to the voice to read out

`_tool_look_at_screen` returns four things to the voice model:

* `answer`;
* **`observations[:8]`**, the readings the vision model is told to make
  concrete ("piece positions");
* the highlight labels;
* their tags.

Then:

* The realtime rules say "say where the highlight is", and nothing says
  "don't read the readings out".
* So the voice narrates the board. When the readings disagree with each
  other, it recounts aloud: "Let me recount. Black has 7 pawns…".
* The card shows the voice's transcript, list markers included.

### 3. A general vision model both reads the board and picks the move

"Best move" matches both `_ADVICE_RE` and `_DENSE_RE`. That sends it to
`gemini-3.1-pro-preview` with:

* a 1,280-px shot of the **whole screen**: a board of about 500 px, so each
  square is about 60 px;
* a request for the best move, in prose.

The model has to identify 32 pieces on 64 squares. The recount is the
tell: it is not sure. A language model's opinion is also not a chess
engine's, and nothing checks that the move is legal.

### 4. Slow by construction

The total is 10–25 s before anything useful is drawn. It adds up from:

* the deep model with a 1,536-token thinking budget and a 35 s timeout;
* "say 'let me look' first" in the realtime rules;
* a full-screen capture on every question;
* no reuse between questions in the same game: every "and now?" reads the
  whole board again.

### 5. Nothing measures a question's answer

`tora.log` records `tool look_at_screen -> ok`, with:

* no timings;
* no model;
* no tokens;
* nothing about what was drawn.

The "guide scorecard" (LG-09) covers walkthroughs only. So a complaint like
this one arrives with no numbers.

### 6. (To check) The card left the screen

`FlowBar._place_card` clamps the card to the pill's screen, yet the second
photo shows it cut off at the right edge. A multi-monitor or mixed-scaling
mismatch between the saved dock's screen and the real one is the suspect.
It needs a repro before it is called a cause.

---

## The shape of the fix

This is the general path, with chess as the first specialist:

1. **Understand the ask.** "What should I do / best move / which one / where
   do I click" is a *next-action* question. The answer is something drawn,
   not a description. The voice model's choice of `look_at_screen` plus a
   local pattern decides it, with no extra model call.
2. **Read exactly.** Use structured data before pixels. On chess.com and
   lichess, read the move list from the page and replay it into the exact
   position. Look at pixels only when that is impossible, and only at the
   board.
3. **Decide on the PC.** A chess engine picks the move: free, instant,
   legal.
4. **Draw one thing.** One arrow from the piece's square to the target
   square. The squares come from where the board is on screen, not from a
   guessed box per move.
5. **Say one line.** The tool hands the voice a `say` line, and the voice
   says that and stops.

The new parts live inside `look_at_screen`, so no new tool is declared.
Every live turn already pays for the declarations, and this adds none.

---

## Milestones

### SN-01 · Measure every answer, and save a chess test set — S — **timeline and scorecard done; the test set moves to SN-04, the baseline waits for a yes**

The timeline and scorecard are done (wave 1). The screenshot test set is
built with SN-04, which renders the boards. The baseline waits for a yes.

**Done:**

* **A timeline line per look-type answer:**
  * question end;
  * tool called;
  * look done: path (specialist or vision), model, tokens in and out;
  * first drawing;
  * first audio.
* **An answer scorecard line,** like the guide scorecard.
* **A test set.** About 20 saved screenshots of chess.com and lichess
  games:
  * white at the bottom and black at the bottom;
  * light and dark board themes;
  * opening, middlegame and endgame;
  * the move list shown and hidden.

  Each comes with its true position (FEN) and its move list. Every
  milestone below tests against these, offline.

*Cost:* the baseline needs today's path replayed on 5 of the screenshots,
5 deep calls ≈ **$0.15**. It waits for a yes.
*Proof:* a scripted look writes the timeline line; the test set loads; the
baseline numbers replace "from the code" in the table above.

### SN-02 · One line, never thinking out loud — S — cost goes *down* — **done**

**Done:**

* **`look_at_screen` hands the voice two things:**
  * `say`, one sentence;
  * `drawn`, what is on screen now.

  The readings stay in `tora.log` for diagnosis and never reach the voice.
  That means fewer tokens into the live turn.
* **The vision prompt.** `answer` is one sentence that states the action:
  no hedging, no counts unless the user asked for counts.
* **The realtime rules:** "When `look_at_screen` returns `say`, say it and
  stop. Never read out what it saw, and never recount. If it could not
  read the screen, say that in one line."
* **The card** strips list markers.

*Proof:* the photo's situation is replayed with a recorded tool result. What
is spoken is one line, and "pawns" is not in it.

### SN-03 · A move is an arrow — S — **done; the model's side is confirmed in the SN-01 baseline**

The code is done. Whether the vision model actually returns `move` with a
`to_box` is checked in the SN-01 baseline. A recorded reply was shown live
(5 Oct): one arrow b1→c3 and the line in the card, with no circles.

**Done:**

* **The vision schema** gets a `move` action, with `from` and `to` boxes.
  It is drawn as one `ARROW` from centre to centre, with no labels on the
  board.
* **For a next-action question:**
  * the old drawings are cleared first;
  * at most one action is drawn;
  * the voice model is told never to add circles on top of a move arrow.
* **It is general:** "drag this there", "move X into Y" use the same
  action.

*Proof:* a recorded vision reply b1→c3 draws exactly one arrow, from b1's
centre to c3's centre, and nothing else.

### SN-04 · Find the board once: where it is and which way up — M

**Done:**

* **From the page first.**
  * **Spike:** does UI Automation give the board element's rectangle on
    chess.com and lichess, in Chrome and in Edge?
  * Otherwise, **one fast vision call**, thinking off, on the normal
    1,024-px shot, for the board's box and whether white is at the
    bottom.
* **Kept per window** until it moves, resizes or scrolls.
* **Square centres are worked out** from the board's rectangle and which
  way up it is. Arrows land exactly, with no box guessed per move.

*Cost:* at most one fast call per board, then nothing.
*Proof:* on the test set, every square centre is within 15% of a square's
width of the truth, both ways up.

### SN-05 · The exact position, from the moves — M — free

**Done:**

* **Read the move list.**
  * **Spike first:** is the move list in the UI Automation tree on
    chess.com and lichess, in Chrome and in Edge?
  * Where it is not, read the move-list panel with the local OCR
    (`hybrid_pointer`'s), on that panel only.
* **Replay the moves** into the position. This needs the move generator
  from SN-06.
* **Whose turn it is** comes from the move list.

*Proof:* the test set's screenshots with a visible move list, 100% of
them, give the exact FEN, with no network.

### SN-06 · The move is chosen on the PC — M — free — **needs your decision**

**Done:**

* **An engine chooses the move.** It is always legal. Mate in one and free
  material are never missed.
* **The "why" is made from the move itself,** with no model:
  * captures, checks, threats;
  * development, castling;
  * "stops their threat on f7".
* **One move, under a second.**

**Choose one:**

| | engine | strength | size and licence |
|---|---|---|---|
| **(a)** recommended | A small engine written here (move generator plus search), pure Python | Good club level at depth 3–4 in about 1 s. Right for "a solid move". | No new dependency, no licence question |
| (b) | Stockfish, run as a separate program | Far stronger than anyone using this | GPL-3, shipped as its own `.exe` with its licence; adds tens of MB to the installer |
| (c) not recommended | the `python-chess` library | (move generation only) | GPL-3 and imported into our code, which raises a licence question for the whole app |

*Proof:*
* every test position gets a legal move;
* a set of about 30 tactics (mate in one, hanging pieces, forks) is solved;
* ≤ 1 s on this PC.

### SN-07 · Pixels only when the moves cannot be read — M — cheaper than today

**Done:**

* **When it applies:** there is no readable move list (a puzzle, a
  screenshot, a video, a book diagram).
* **How it reads:** the shot is cropped to the board (SN-04), and the
  **fast** model, thinking off, is asked for the position as FEN with a
  strict schema.
* **Every read is checked:**
  * one king each;
  * at most 8 pawns a side;
  * no pawn on the first or last rank;
  * whose turn it is: asked once if the screen does not say.
* **If the check fails,** it says one line: "I can't read this board
  clearly - can you show me the move list?". It never guesses.

*Cost:* a crop of the board, instead of the whole screen at 1,280 px, on
the fast model without thinking. That is cheaper per question than
today's deep look.
*Proof:*
* ≥ 18 of the 20 test screenshots read to the exact position. This is
  measured: 20 fast calls on crops, **< $0.02**. It waits for a yes.
* Every failed read says so in one line.

### SN-08 · One path for "what should I do next?" — M

**Done:**

* **Inside `look_at_screen`.** A next-action question goes to a
  specialist that recognises the screen:
  * chess first, by the window title or URL (chess.com, lichess), or by
    SN-04 finding a board;
  * otherwise, vision with the action schema from SN-03.
* **A specialist returns `{say, drawing}`.**
* **Follow-ups in the same game** ("and after that?", "what if he
  takes?") use the kept board and position. There is no new look.
* **The same contract for other next-action questions.** "Which one
  should I pick?" gets one ring or arrow on the choice and one line.

*Proof:*
* Scripted end to end on the test set, from question to one arrow and
  one line, in ≤ 2 s on the move-list path, with 0 vision calls.
* A follow-up makes 0 calls.

### SN-09 · The card stays on screen, and off the answer — S

**Done:**

* The clipped card in the second photo is reproduced: two monitors, mixed
  scaling, docked right.
* The card is clamped to the screen it is actually on.
* **The card never covers what Tora drew.** In the wave-1 demo, the card,
  docked at the bottom, covered the board's first rank: the knight the
  arrow starts from. The user's photo shows the same thing. While a
  drawing is on screen, the card moves to the side of the screen away
  from it.

*Proof:*
* a flow-bar test across mixed-scale monitors: the card is inside the
  screen in every dock position;
* with an arrow drawn under the card's place, the card sits clear of it.

### SN-10 · A live acceptance run — S (needs your yes)

**Done:** "what's my best move?" is asked live:

* 10 times on chess.com;
* 10 times on lichess;
* playing white and playing black;
* with follow-ups.

**Pass:**
* every number in the "done" table;
* 20/20 arrows on the right squares;
* 20/20 legal moves;
* no narration;
* cost per answer ≤ today's, from the scorecard.

*Cost:* the live turns, plus vision only where SN-07 runs: about
**$0.10–0.30**.

---

## Build order

| wave | milestones | what you will notice |
|---|---|---|
| 1 | SN-01, SN-02, SN-03 | One line and one arrow instead of a lecture. The board is still read by vision, now measured. |
| 2 | SN-04, SN-05, SN-06 | The arrow is on the right squares, the move is real, it is under 2 s, and it costs nothing. |
| 3 | SN-07, SN-08 | Any board works. Follow-ups are instant. "Which one should I pick" uses the same path. |
| 4 | SN-09, SN-10 | The card stays on screen, and a live run proves it. |

Waves 1 and 2 are local code tested against the saved screenshots, with
no paid calls except SN-01's baseline.

## Cost

* **Per answer, after wave 2:** no vision call on chess.com and lichess
  when the move list can be read. The live turn is shorter too, since the
  voice no longer receives the readings.
* **Building it** needs a few paid calls, and each waits for a yes:

| milestone | what | about |
|---|---|---|
| SN-01 | baseline | $0.15 |
| SN-07 | reading accuracy | < $0.02 |
| SN-10 | live run | $0.10–0.30 |

* **No new third-party dependency**, unless you choose Stockfish in SN-06.

## Decisions needed

1. **SN-06's engine:**
   * **(a)** written here (recommended);
   * **(b)** Stockfish (strongest; GPL, shipped as a separate program, a
     bigger installer).
2. **The measured runs:** about $0.45 in all, across SN-01, SN-07 and
   SN-10.

## Out of scope

* **Making the move for the user,** by clicking or dragging the piece.
* **Coaching:** analysis lines, mistakes in the game so far, openings.
* **Other games** (checkers, sudoku, cards). Each is a later specialist
  on the SN-08 contract.
