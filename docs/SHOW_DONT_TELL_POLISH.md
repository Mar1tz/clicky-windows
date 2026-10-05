# Show, don't tell — polish: every milestone, in order

**Written for:** whoever works through these, one at a time (the owner,
Dylan, or a Claude session). Each milestone stands on its own:

* why it matters, with the evidence;
* what to do, and where;
* how to prove it, and what proving it costs.

Do them in order unless a milestone says otherwise. The history and the
measurements behind them are in
[SHOW_THE_NEXT_MOVE.md](SHOW_THE_NEXT_MOVE.md).

**The goal (unchanged):** Tora understands what the user wants and shows
it. That means one thing drawn on the right spot and one short line said.
It should be **fast**, and **never dearer per message**.

---

## Where it stands (HeyTora 1.7.1, live 6 Oct)

* **The answer:** a what-to-do question gets one thing drawn (a move is
  an arrow) and one line said; the screen is never read out.
* **The model:** looks start on the fast model, and go deep only when it
  is not sure, has nothing to draw, or for dense reading or a game move.
  Measured on 16 screens: right on 15, ~$0.0014 and ~2.6 s per look.
* **The image:** the look sees the window in front, with the mouse as a
  hint.
* **The drawing:** rings snap onto the real control. Checked live: 5 ms,
  exact.
* **The safety nets:** an answer that names a target always gets its
  arrow; asking again on an unchanged screen makes no call.
* **Measured:** every look is timed and priced in `tora.log`, as the
  *answer timeline* and the *answer scorecard*.
* **The card:** stays on screen and moves off what Tora drew.

**Committed but not released:** `bdd4afe`, which stops crash.log calling
handled Windows errors crashes.

---

## How to work a milestone

1. **Read this milestone.** If it touches the look, also read
   `ai/gemini_vision.py`, `ai/look_region.py` and `_tool_look_at_screen`
   in `companion_manager.py`.
2. **Measure before and after** when it changes the look or the voice:
   SP-01 for the look, SP-02 for the voice. Write both numbers in the
   commit.
3. **Keep the suite green:** `python -m pytest tests/agents -q`. Add tests
   beside the existing ones (`test_e2e_look_fast_and_exact.py`,
   `test_e2e_never_words_never_twice.py`, `test_e2e_card_keeps_clear.py`).
4. **Show anything visible on screen first** (the owner's rule), then
   commit.
5. **Mark the milestone done** here, with its numbers.

**Paid runs:** say the estimate before running, and stop at it. Never
send anything but test screens: on 5 Oct a background script could not
take the focus, and 16 looks saw the owner's editor instead.

---

## Wave A — measure first (everything else is judged by these)

### SP-01 · A look test bench in the repo — M — **done (6 Oct)**

**The first run,** in the app's routing ($0.081): **22/23 right**, p50
2.6 s, avg $0.0035 a look, 20 fast and 3 deep. The baseline,
`tools/look_eval_baseline.json`, is now SP-04's run (avg $0.0020); compare
with `--against tools/look_eval_baseline.json`.

**What it already found:**
* **The only wrong one** is the music screen: "what should I play next?"
  went to the deep model. That is SP-08.
* **The fast model thought** on 14 of 23 screens, up to 338 tokens,
  despite budget 0. That is SP-04.
* **The deep model** spent **2,050** thinking tokens on the chess move
  (over its 1,536 budget): $0.037 and 20 s for one look. The deep look's
  thinking is not controlled either, so SP-04 covers both models.

**Why:**
* The fast-first decision rests on one run of a script that lived in a
  session's scratch folder, and is gone.
* Every later change to the look (thinking, image size, wording,
  routing) needs the same before/after measurement.

**Do:**
* **The tool:** `tools/look_eval.py`. **Start from it:** the 5 Oct script
  is committed there as a starter, with all 16 screens and their targets.
  `--render` rebuilds them for free.
  * Test screens are built as HTML and rendered to PNG headless (Edge, the
    same way as `agents/tools/design_tools.render`), so **nothing opens on
    screen**.
  * Each screen goes through `gemini_vision.look()`, the exact routing the
    app uses.
* **It reports**, per screen and in total:
  * right thing drawn: the drawn box's centre inside the target's known
    rectangle, or a move's two ends in their squares;
  * fast, deep, or fast→deep;
  * seconds;
  * tokens in, out and thinking;
  * cost.
* **Flags:** `--fast-only`, `--max-dollars` (stop when reached), `--only
  name,name`.
* **The screens:**
  * **The 16 from 5 Oct** (their list and targets are in
    SHOW_THE_NEXT_MOVE.md, UD-01): sign-in, switches, products, chart,
    spreadsheet, dialog, inbox, checkout, the CC icon, form, files,
    tic-tac-toe, worksheet, menu, payment, chess mate-in-one.
  * **Plus the cases that break things:**
    * a dark theme;
    * a toolbar of similar unlabelled icons;
    * a tiny target (a 16 px "x");
    * a dialog over a busy page;
    * a non-English UI;
    * a **decoy**, where the thing asked for is *not* there and the
      right answer is to say so and draw nothing;
    * "what should I play?" on a music app (not a game: must stay fast).
* **Credentials:** the owner's own key, `TORA_ACCESS=own_key`, never the
  plan.

*Prove:* it reproduces 5 Oct (fast right on 15/16, chess right only via
deep). A `--fast-only` run costs about $0.03.
*Size:* M. *Cost to build:* about $0.10 of runs.

### SP-02 · A voice test bench — M — **done (6 Oct)**

**The first run** ($0.116): **12/12 passed**. The answer's first word came
at p50 1.4 s. It is the baseline, `tools/voice_eval_baseline.json`; compare
with `--against tools/voice_eval_baseline.json`.

**What it found:**
* **The voice behaves.** Every what-to-do question called the look (5 of
  5), and it said the `say` line and stopped. It never read out the
  misleading look's extra rings.
* **"Let me look" costs time.** In 2 of 6 look questions it said a filler
  first ("Let me analyse the board…"):
  * the answer then came at **4.4–4.9 s instead of 1.0–1.5 s**;
  * the look was called a second later (1.6 s, not 0.55 s);
  * the server makes all of the filler's audio before it starts the
    answer.
  * That is SP-17.
* **The voice costs more than the look.** See SP-03 and SP-16.

**Why:**
* Nothing has checked the *voice* side since wave 1:
  * that the live model calls `look_at_screen` for "what should I do?";
  * that it says the `say` line and stops;
  * that it never narrates.
* It was never tested live because that needs someone speaking.

**Do:**
* **The tool:** `tools/voice_eval.py`. It opens a live session
  (`ai/gemini_realtime.py`) and asks by **text**, with
  `LiveSession.send_text`. There is no microphone and no playback: the
  audio is dropped, and the output transcription is kept.
* **The tools answer from recorded results**, so no screen is captured. A
  `look_at_screen` call returns a canned `{say, drawn, highlights}`.
* **About 12 questions:**
  * "best move", "what should I click next", "which one should I pick";
  * a where-is, a how-to (it must call `show_me_how`);
  * small talk (it must call no tool);
  * one with a misleading canned look, where the voice must still say only
    the line.
* **Checks:**
  * the right tool was called;
  * what was said is at most two sentences;
  * it contains no "let me", no counts, and nothing from the canned
    readings;
  * the time to the first transcript.

*Prove:* a report line per question, and a pass on 12/12 before any
prompt change ships.
*Cost:* about $0.12 a run (measured; the estimate was $0.05).

### SP-03 · The whole answer's cost, not just the look — S — **done (6 Oct)**

**Measured on SP-02's run:**
* **The voice: $0.0097 an answer.**
  * A question that calls a tool: $0.009–0.012.
  * Small talk: $0.0054.
  * A look adds $0.0035 (SP-01), so **a looked answer is about $0.013,
    and three quarters of that is the voice.**
* **Why it costs that.** Every generation bills the whole prompt, about
  4,500 tokens:
  * 3,850 of them are text: the rules (~1,700) and the tool list
    (~2,100);
  * a question that calls a tool is two generations, so it pays for them
    twice.
* **The live model thinks** 56–273 tokens an answer (~$0.0008).
* **How the server reports it.** One `usageMetadata` per generation, just
  before `turnComplete`. Its modality breakdown is about 460 tokens short
  of the count; the gap is priced as text.
* **Prices:** `gemini-3.8-live`, from Google's list on 6 Oct, in
  `gemini_live.LIVE_PRICES`. Per million tokens: $0.75 text in, $3 audio
  in, $4.50 text out, $12 audio out.

**In `tora.log`:**
* The answer timeline ends with "voice N in / N out $x".
* After a "let me look", the line waits for the answer, and shows "answer
  +Ns" beside the first word.
* The scorecard gets "voice cost avg" and "answer cost avg", per question
  asked.

**Why:**
* The scorecard prices the look only.
* The live voice turn (audio in, the system prompt, the tool results,
  audio out) is probably most of a message's cost, and is never
  measured.
* "Never dearer per message" cannot be proved without it.

**Do:**
* **Read the voice's tokens.** `ai/gemini_live.py` reads the
  `usageMetadata` the live server sends with each turn, and
  `RealtimeConversation` hands it to the manager with the turn.
* **Add it to the answer.** The answer timeline gets "voice N in / N out",
  and the scorecard gets "voice cost avg" and "answer cost avg".
* **Price it.** Live audio prices go in the same table, priced from
  Google's current list; check it when this is built.

*Prove:* a scripted turn carrying a usage message shows up on the
timeline. On SP-02's run, the cost per answer is printed.
*Size:* S.

---

## Wave B — the fast look, faster and cheaper

### SP-04 · Thinking under control, fast and deep — S — **done (6 Oct)**

**What Google's docs say:** Gemini 3 takes a thinking *level* (low,
medium or high; medium if unsaid). It takes no budget, and thinking has no
"off". `gemini-3.8-flash` and `gemini-3.1-pro-preview` both go down to
"low".

**What changed:**
* `gemini.thinking()` sends budget 0 as `thinkingLevel: "low"` on Gemini 3.
* A caller may name a level. The deep look asks for "low"
  (`_DEEP_THINK`).
* The planner's 2,048 budget is unchanged: no bench measures it yet.

**SP-01, before → after** (in the app's routing; $0.075 with two
repeats):

| | before | after |
|---|---|---|
| right | 22/23 | 22/23 |
| average look | $0.0035 | **$0.0020 (−42%)** |
| thinking tokens | 3,663 | 1,405 |
| deep looks: cost | $0.054 | **$0.020** |
| deep looks: p50 | 5.3 s | 3.9 s |
| chess | 20 s, $0.037 | **3.7–4.0 s, $0.0066**, right 3 of 3 |

* **The fast model did not change.** Its thinking stayed at about 1,230
  tokens, on 10 of 20 screens, up to 274. "low" is as little as it takes:
  budget 0 was already being read as low.
* **The new baseline** is this run (`tools/look_eval_baseline.json`).
* **Only three deep screens** are measured. If a harder deep question
  (dense code, a long contract) is ever missed, try "medium" for
  `needs_reading` only.

The SP-01 baseline backed it up:
* **Fast:** thinking on 14 of 23 screens, up to 338 tokens.
* **Deep:** 2,050 thinking tokens on chess against a budget of 1,536, at
  $0.037 and 20 s.
* **The fix covers both:** the fast model as close to none as it allows,
  and the deep model at a level that still solves the mate-in-one.

**Why:**
* `gemini.thinking()` sends `thinkingBudget: 0` for the fast model.
* On 5 Oct's windowed run, `gemini-3.8-flash` still reported **59, 67,
  84, 159, 215 and 613 thinking tokens**.
* Gemini 3 models take a *thinking level*, and the old budget is not
  doing what it says.
* At $3 per million, 613 thinking tokens is **$0.0018: more than the rest
  of the look**, and a few seconds.

**Do:**
* Check Google's current docs for how Gemini 3 Flash is told to think as
  little as possible ("minimal", if it exists).
* Send that from `gemini.thinking()` for the fast model. Keep the budget
  only for models that still take it.

*Prove:*
* SP-01 `--fast-only`: thinking tokens ≈ 0 on every screen;
* right-thing-drawn no worse;
* p50 time and cost down. Write the before and after.

*Cost:* about $0.06.

### SP-05 · Fewer words back — S

**Why:**
* The look writes `observations` on every call. They are only logged
  now (wave 1), never used.
* Output is **6× the price of input** on Flash and is the slow part:
  fast looks returned 63–163 output tokens.

**Do:**
* Drop `observations` from the fast look's schema, or cap them at three
  short items, and keep `answer`, `actions` and `sure`.
* The deep look may keep them: they are its reasoning trail in the log.

*Prove:* SP-01 output tokens and p50 time down, with accuracy unchanged.
*Cost:* about $0.03.

### SP-06 · The right image size — S

**Why:**
* A fast look's prompt was about 1,450 tokens, almost all of it the
  1,024-px image.
* Gemini 3 has a media-resolution setting. Most UI questions may not need
  the top one, while dense reading does.

**Do:**
* With SP-01, try the low, medium and high resolutions on every screen.
* Pick one per class: everyday UI; dense reading and game boards (keep
  high).
* Set it in `gemini_vision._ask`.

*Prove:* the chosen setting keeps accuracy and lowers tokens and time.
*Cost:* about $0.10 (three resolutions × 23 screens, fast).

### SP-07 · Typed questions follow the same rule — S — free

**Why:** `ai/gemini_provider.py` (the typed path, and the fallback when
the live session is down) still picks the deep model with `wants_deep()`.
That is the rule UD-01 replaced: 11 of 20 everyday questions went deep on
their words alone.

**Do:**
* `pick_model` uses `needs_reading()` / `needs_judgement()`, and the fast
  model otherwise.
* A typed question cannot escalate on `sure`, because it streams. If
  deep answers are missed, add a "think harder" retry.

*Prove:* unit tests on the same 20 questions; a typed "which one should I
pick?" goes fast.

### SP-08 · Sharper routing words — S — free

**Why:** `_GAME_RE` treats "play" as a game word, so "what should I play
next?" on Spotify goes to the deep model.

**Do:**
* "play" and "win" count only alongside a game word (board, move, chess,
  game, card, puzzle), or with a game site's window title (chess.com,
  lichess and the like).
* SP-01's music screen holds it.

*Prove:* unit tests both ways, and SP-01's music screen stays fast.

### SP-16 · A lighter live prompt — S — **found by SP-03**

**Why:**
* The voice is three quarters of a looked answer's cost (SP-03).
* Most of that is the same 3,850 tokens of rules and tool list, billed
  on every generation: twice for any question that calls a tool.
* **Two places say the same thing.** The rules and the tool descriptions
  overlap: look_at_screen's when-to-use is written out in both.
* **Long and rarely used.** `delegate_task`, `desktop_action` and
  `manage_agents` together are about half the tool list.

**Do:**
* Say each thing once:
  * when to use a tool goes in its description;
  * how to speak goes in the rules.
* Shorten the long descriptions without losing a rule a test holds.
* Measure with SP-02 before and after.

*Prove:* SP-02 still 12/12; prompt tokens down by at least 1,000 (about
$0.0015 an answer, 15%).
*Cost:* about $0.25 (two runs).

### SP-17 · "Let me look", or straight to the look — S — **found by SP-02**

**Why:**
* The rules say to speak first, then call a slow tool. On SP-02's run,
  that delayed the answer by about 3.5 s (SP-02's findings).
* With a real look (about 2.6 s), the filler fills the silence but
  probably still costs about a second.
* Meanwhile the island and the flow bar already show that Tora is
  looking.

**Do:**
* Try both with SP-02, on the look questions:
  * **(a)** call the look first, and say nothing until it answers;
  * **(b)** keep a filler, but two words ("One sec.").
* Take a real look's time from SP-01's p50. Keep whichever has the answer
  sooner, as long as nothing is silent for more than about 1.5 s
  without the island showing it.

*Prove:* SP-02 answer p50 on the look questions down; 12/12 kept.
*Cost:* about $0.25.

---

## Wave C — draw exactly, where they mean

### SP-09 · Snap to the control they mean, not the text inside it — M — free

**Why:**
* UD-03 snaps to the element `ControlFromPoint` returns. That is the
  **deepest** one: in a browser, often the label inside a button, not the
  button. The ring then sits on the word.
* Moves (a file dragged to a folder) are not snapped at all.

**Do:**
* `look_region.control_rect_at` walks up the ancestors.
* Prefer clickable types (Button, Hyperlink, Edit, CheckBox, RadioButton,
  MenuItem, ListItem, TabItem, ComboBox) whose size is closest to the
  model's box, within the same 3× rule.
* Snap a move's two ends the same way.

*Prove:*
* unit tests with a fake tree (label inside button → the button);
* a live check, no model: a box round a browser button's label snaps to
  the button.

### SP-10 · Look at the monitor they point at — S — free

**Why:**
* With several monitors, the look takes the monitor of the window in
  front.
* When someone says "what's **this**?" with the mouse on another monitor,
  Tora looks at the wrong one. The owner has three.

**Do:**
* In `_tool_look_at_screen`, when the question points (`pointer_hint`'s
  words) and the mouse is on another monitor, look at that monitor: its
  shot, or the window under the mouse.

*Prove:* a unit test with two fake monitors; a pointing question with the
mouse on the second one looks there.

### SP-11 · The arrow's head is visible; the card glides — S — free

**Why:**
* **The cursor covers the arrowhead.** In the wave-1 demo, Tora's cursor
  rested on the arrow's tip for about 2.5 s after drawing (it rides the
  pen tip), covering the arrowhead.
* **The card jumps.** In the SN-09 demo, the card jumped to the screen's
  edge when a drawing came.

**Do:**
* **The cursor:** when a stroke ends, it eases about 40 px back along the
  arrow before resting (`ui/overlay.py`, `_tick` drawing mode).
* **The card:** `FlowBar._place_card` slides the card over about 0.25 s
  instead of moving it at once.

*Prove:* overlay and flow-bar tests on the positions over time; shown live
(the owner's rule).

### SP-12 · The card follows what is really on screen — S — free

**Why:**
* The card forgets a drawing after 20 s (`AVOID_FOR_S`).
* A walkthrough's drawings stay until the step ends, so the card can slide
  back over a drawing that is still there.

**Do:**
* The card asks the overlay what is drawn now (its annotations, the ring,
  the pointed target), instead of keeping its own list with a timer.

*Prove:* a test with a guide drawing kept 60 s; the card stays clear the
whole time.

---

## Wave D — faster still, and numbers from real users

### SP-13 · Start looking before she is asked — S — **only if the numbers say so**

**Why:**
* The voice hears the question, says "let me look", then calls the look.
* Starting the fast look when the words arrive would save about 1–2 s.
* But a look started and not used is money spent for nothing.

**Do:**
* **The condition.** Only if SP-02 and SP-03 (or SP-14's field numbers)
  show the voice calls `look_at_screen` for at least 90% of
  `is_next_action` questions. SP-02's first run: 5 of 5. That is a
  small sample; SP-14's numbers would settle it.
* **What it does.** Start the fast look as soon as the heard words match.
  The voice's call then picks it up through UD-05's reuse.
* **The record.** Count unused prefetches on the scorecard.

*Prove:*
* p50 time to the drawing down by at least 1 s;
* unused prefetches under 10%;
* cost per answer not up.

### SP-14 · Numbers from real users, if they agree — M — **needs your decision**

**Why:** the chess complaint came from a PC whose `tora.log` nobody could
read. Without numbers from users, no one knows whether answers are right,
fast and cheap out there.

**Do:**
* **Opt in.** A switch, off by default: "Help improve Tora: send anonymous
  answer timings".
* **Once a day,** send the scorecard's numbers:
  * looks;
  * nothing drawn;
  * p50 times;
  * fast/deep/reused counts;
  * average cost.
* **Never** screen content, words, titles or app names.
* **The server side:**
  * a Supabase table with a retention limit;
  * a migration (next number after 0007);
  * an admin view on the owner's site.

*Prove:* the payload is tested to contain only numbers. A user with the
switch off sends nothing.
*Decision:* whether to ask users at all, and the wording.

### SP-15 · Ship it, and check it went out — S

**Why:**
* `bdd4afe` (crash.log) and everything above reach users only in a
  release.
* On 6 Oct, 1.7.1's GitHub Pages build sat at "building" for over 10
  minutes. `latest.json` stayed at 1.7.0 until Pages caught up, so no
  copy could see the update.

**Do:**
* **Release 1.7.2,** the usual way: dry run, the real self-update with
  relaunch on the owner's copy, then `--skip-build`.
* **`tools/release.py`, after pushing the site:**
  * watch the Pages build (`gh api repos/Qwickly/heytora/pages/builds/latest`);
  * if it is still "building" after 5 minutes, request a new build once;
  * then wait for `latest.json` to show the version.
* **The gh account.** The script needs `Qwickly` active. On 6 Oct another
  program had switched gh to a different account and the first publish
  failed. The script should say so instead of failing on `gh release
  create`.

*Prove:* the live `latest.json` shows 1.7.2, and the installed copy
updates to it.

---

## Parked: exact chess

These help chess alone. They are kept, not planned.

* **The engine:** built (`f48da3d`, `specialists/chess`). It is legal,
  answers in about 0.8 s, and says why. It is only useful with an exact
  position.
* **Reading the move list:** lichess's move list is not in the
  accessibility tree (5 Oct spike).
* **Board position and position-from-pixels:** not started (SN-04,
  SN-07).

Until then, a chess question goes to the deep model first (UD-01), which
answered the mate-in-one correctly.

---

## The order, at a glance

| # | milestone | wave | size | cost to prove | needs |
|---|---|---|---|---|---|
| SP-01 | Look test bench — done | A | M | ~$0.10 | – |
| SP-02 | Voice test bench — done | A | M | ~$0.12/run | – |
| SP-03 | Whole answer's cost — done | A | S | (with SP-02) | – |
| SP-04 | Thinking under control — done | B | S | ~$0.08 | SP-01 |
| SP-05 | Fewer words back | B | S | ~$0.03 | SP-01 |
| SP-06 | Right image size | B | S | ~$0.10 | SP-01 |
| SP-07 | Typed path, same rule | B | S | free | – |
| SP-08 | Sharper routing words | B | S | free | SP-01 screen |
| SP-16 | A lighter live prompt | B | S | ~$0.25 | SP-02 |
| SP-17 | "Let me look" or not | B | S | ~$0.25 | SP-02 |
| SP-09 | Snap to the real control | C | M | free | – |
| SP-10 | The monitor they point at | C | S | free | – |
| SP-11 | Arrowhead visible, card glides | C | S | free | live demo |
| SP-12 | Card follows the real drawings | C | S | free | – |
| SP-13 | Look before asked | D | S | measured | SP-02/03 or SP-14 |
| SP-14 | Numbers from real users | D | M | free | **your decision** |
| SP-15 | Ship 1.7.2, verified | D | S | – | release |

**Quick wins that need no paid runs:** SP-07, SP-08, SP-09, SP-10, SP-11,
SP-12.

**The biggest levers:**
* **Cost:** SP-16. The voice is three quarters of a looked answer
  (SP-03).
* **The answer's speed:** SP-17.
* **Done:** SP-04 took the deep look from $0.054 to $0.020 and from 5.3 s
  to 3.9 s.

Each has its bench (SP-01 or SP-02), so nothing changes without a
before-and-after.
