# Show it by tracing: the milestones

**Written for:** whoever builds these (the owner, Dylan or a Claude session).
Each milestone stands on its own: why, what to do and where, and how to
prove it. Do them in order unless one says otherwise.

**What was asked (10 Oct 2026):** "When I'm holding down the shortcut it can
use my cursor to move around, and it leaves a little ghost trail behind it,
like mist. Then the AI must know to focus on this area of information,
based on the question."

**The goal:**
* Hold Ctrl+Win, move the mouse over what you mean, and ask.
* A soft mist follows the cursor, so you can see what you have shown.
* Tora's answer is about exactly that: the paragraph you underlined, the
  chart you circled, the two things you pointed at, in that order.
* It is as fast as an answer without a trace: what is under the trace is
  **read, not photographed**. A screenshot is taken only when the traced
  thing is a picture. This follows the owner's word on the same day: "it
  mustn't take screenshots… it needs to be instant".

---

## What is already there (reviewed 10 Oct)

Read from the code, not guessed.

1. **The hold is known.** `companion_manager.on_hotkey_press` and
   `on_hotkey_release` arrive on the keyboard hook's thread (main.py
   `GlobalHotkeyMonitor`).
   * A press starts listening.
   * A release after `cfg.tap_max_seconds` ends the question.
   * A shorter one is a tap, and Tora goes on listening hands-free.
   * So a trace can start at the press. Whether it was a hold is known
     only at the release.
2. **The mouse is read already, in physical pixels.** That is what
   `look_region.cursor_position()` returns, the process being per-monitor
   DPI aware. The overlay reads `QCursor.pos()` (Qt's logical pixels) on
   every frame of its 60 fps `_tick`. With the owner's mixed 100/125%
   monitors the two differ. Convert through `screen/monitors.py`; this is
   a known trap.
3. **The overlay can draw it.** `ui/overlay.py`:
   * `CursorOverlay` paints the whole scene in desktop coordinates.
   * There is one transparent, click-through `_Surface` window per
     monitor.
   * It repaints only what changed (`_regions` / `rects`), and every third
     frame when at rest.
   * A mist trail is one more layer in `_paint`, under the buddy.
   * ⚠ **This file holds the SP-11/12 work, still uncommitted** (the
     cursor resting off the arrowhead, and the card keeping off drawings).
     Decide SP-11/12 before TF-02, or build on top of it.
4. **The mouse is used today, as one point.** `look_region.pointer_hint`
   puts "the user's mouse pointer is at x, y" into a look, but only when
   the question says this, here or which one. `points_at_mouse` and
   `frame_under` pick the monitor under the mouse (SP-10). A trace replaces
   the single point with the shape the user drew.
5. **The voice gets a line about the screen every turn, with no pixels.**
   `_realtime_stream` builds `screen_summary.describe()` (the window title,
   the focus, and up to 28 control names) and sends it while they speak
   (FS-04). A trace is only complete at the release, so it needs **a second
   line, sent at the release, before `end_user_audio()`**.
6. **What is under a point can be read without a picture.** Windows UI
   Automation gives the control at a point (`ElementFromPoint`;
   `look_region.control_chain_at` does this today, in ~5 ms) and the text
   at a point (`TextPattern.RangeFromPoint`, widened to the line or
   paragraph). Page text is already read this way (`ai/uia_page.py`; on
   Gmail, 2,000 characters in 0.3 s).
7. **Tora's drawings and the card** already avoid what is drawn (SN-09,
   SP-12). The traced area should be treated the same way.

---

## How to work a milestone

1. **Read the milestone,** and the code it names.
2. **Measure before and after** where it changes speed or what is right:
   * the answer timeline in `tora.log`;
   * `tools/look_eval.py` (gets traced scenes in TF-06).
3. **Keep `python -m pytest tests/agents -q` green.** Add tests beside the
   existing ones: `tests/agents/test_e2e_trace_*.py`.
4. **Show anything visible on the owner's screen first,** then commit
   (memory: "demo UI live on their screen before committing").
5. **Mark it done here,** with its numbers.

**Paid runs:** say the estimate first. Never send a real screen in a test.

---

## Wave A — the mist

### TF-01 · Record the trace while the shortcut is held — S — free

**Why:** everything else stands on knowing where the mouse went, and when.

**Do** (new `ai/trace.py`, pure and testable, plus `companion_manager`):
* **Start at the hotkey press** (`on_hotkey_press`); stop at the release,
  or when listening ends.
* **Sample `GetCursorPos`** (physical pixels) about every 16 ms on a small
  sampler. The asyncio loop is fine: it is a tuple per tick. Record
  `(t, x, y)`, with `t` measured from the press.
* **Keep it small:**
  * drop moves under 2 px;
  * cap it at about 2,000 points (30 s);
  * thin straight runs (Ramer-Douglas-Peucker, ε ≈ 2 px).
* **Give it to the turn:**
  * `Trace` lives on the request (`_request_serial`);
  * a tap with no movement means no trace;
  * a trace is cleared at the next press.
* **Thread-safety:** press and release arrive on the hook thread. Hand
  them to the loop (`_submit`), as the existing paths do.

*Prove:*
* unit tests: points in, a thinned path out;
* jitter dropped;
* a tap is no trace, and a hold with a move is one;
* the cap holds.

### TF-02 · The ghost trail of mist — M — free

**Why:** the user must see what they have shown, as they show it, and it
should feel alive (like the agents' small animated orbs).

**Do** (`ui/overlay.py`, after the SP-11/12 decision; see finding 3):
* **The mist is puffs left along the cursor's path.**
  * They are emitted every ~6 px, and interpolated between frames, so a
    fast sweep leaves a continuous ribbon, never dots.
  * Each puff is a soft radial blob. It grows a little (×1.4), drifts a
    few pixels, and fades out over ~1.2 s.
  * The colour is Tora's own (the colour the user picked for her cursor),
    at low alpha, with a whiter core: mist, not paint.
* **Cheap at 60 fps:**
  * render the blob once to a cached `QPixmap` per colour, and draw it
    scaled;
  * cap it at about 160 live puffs;
  * repaint only the trail's bounds, in the existing region logic;
  * when no puffs are left, go back to the rest-frame saving.
* **Only while the shortcut is held** (see decision 2). The cursor buddy
  keeps following as now; the mist trails behind it.
* **At the release the trail settles into a faint haze** over the traced
  area, so the user sees what Tora will look at. It fades when she answers,
  or after ~2.5 s (see decision 1).
* **Multi-monitor:** the mist is drawn in desktop coordinates like
  everything else, so it crosses monitors the way the cursor does.

*Prove:*
* offscreen frames (as `render_orbs2.py` did for the orbs): a sweep, a
  circle and a fast flick;
* the frame time stays under ~4 ms with 160 puffs;
* then **live on the owner's screen**, before any commit.

---

## Wave B — what was traced, read and not photographed

### TF-03 · From a trail to what they meant — S — free

**Why:** a scribble over an area, a circle round something, a line under a
sentence and a pause on one spot each mean something different. The answer
needs the thing, not the path.

**Do** (`ai/trace.py`):
* **Split the path into strokes** on a pause (over 250 ms nearly still) or
  a jump.
* **Name each stroke:**

  | name | what the path looks like |
  |---|---|
  | **dwell** | stays within ~12 px for 300 ms or more: a point |
  | **underline** | long, mostly flat, and under text |
  | **circle** | closes on itself (end near start, the path round it): the region inside |
  | **sweep** | back and forth over an area: its bounds |
  | **line** | from one thing to another |

* **Each stroke becomes a `Focus`:**
  * a rectangle (physical pixels) and its monitor;
  * the window under it (`look_region.window_at`);
  * its order;
  * `t0`/`t1` within the hold, which TF-09 uses.

*Prove:* unit tests on drawn paths: a circle, an underline, a sweep, a
dwell, two strokes with a pause, a circle drawn badly (not closed: still a
circle).

### TF-04 · What is under it, read — M — free

**Why:** most of what people trace has words: a paragraph, a table row, a
button, an error message. Read through UI Automation, those take
milliseconds and no pixels leave the PC. That is the owner's "no
screenshots, instant".

**Do** (`ai/trace.py` with `ai/uia_page.py` and `look_region`):
* **Read each `Focus`:**
  * sample points along its stroke (about 8 for a line, a grid of 9
    inside a circle or sweep);
  * the control at each point: `control_chain_at`, kept when it is about
    the stroke's size;
  * the text there: `TextPattern.RangeFromPoint`, widened to the word, the
    line or the paragraph, as the stroke's kind says.
* **The result is a short reading per focus,** for example `underlined:
  "Total due within 30 days" (a paragraph in Edge)` or `circled: the chart
  "Revenue by month" (Excel)`.
* **Read during the hold,** as each stroke ends, so it is ready at the
  release.
* **Time-boxed:** 300 ms in all. What is not read in time stays geometry
  only.
* **Privacy:**
  * nothing in a sensitive window (`is_sensitive_window`);
  * never a password field;
  * reading happens only while the shortcut is held.

*Prove:*
* tests on a fake UI Automation tree;
* live, read-only, on the owner's Gmail and a document: the underlined
  sentence comes back word for word.

### TF-05 · The voice is told at the release — S — free

**Why:** the screen line goes out while they speak (FS-04). The trace is
only complete at the release, and the answer must know about it.

**Do** (`companion_manager._realtime_stream` / `_end_capture_and_process`):
* **At the release, one more context line,** sent before
  `end_user_audio()`, for example: *"While asking, they traced over: (1)
  circled the chart "Revenue by month" in Excel; (2) underlined "Total due
  within 30 days". 'This', 'that' and 'here' mean these, in this order."*
* **It waits at most 150 ms** for TF-04. Otherwise it goes as geometry
  only ("circled an area at the top right of Excel").
* **A new rule in `config._REALTIME_RULES`:** *A TRACE IS WHAT THEY MEAN.
  Answer about the traced thing. Its words are often enough. Call
  look_at_screen only when it is a picture, a chart or a layout; it looks
  at the trace by itself.*
* **The cascade path** gets the same line in its prompt.

*Prove:*
* a test: the trace line is sent, and before the end of the audio;
* the timeline shows `trace line +0.1s` after the release;
* 10 traced questions on the owner's PC: the answer is about the traced
  thing.

---

## Wave C — looking at it, when it is a picture

### TF-06 · The look focuses on the trace — M — about $0.05 of runs

**Why:** a chart, a photo, a chess board or a diagram needs pixels. Then
the model should see the traced part sharply, and know it was traced.

**Do** (`companion_manager._tool_look_at_screen`, `look_region`):
* **The picture:**
  * the traced area, padded by half its size, at full resolution, as the
    main image;
  * the whole window small, for context.
  * One call either way. The crop is a sharper image of fewer pixels, so
    no dearer.
* **The trace drawn faintly into the image** (a thin outline) as well,
  with a hint line in place of `pointer_hint`: *"The user traced the
  outlined area (x 120-480, y 300-520 on this image); the question is
  about it."*
* **The chess path** (`look_chess`) keeps its own crop. A trace on a board
  is only a hint there.
* **`tools/look_eval.py` gets traced scenes:**
  * two charts, and the second is circled: "what's this one's peak?";
  * a table, with one row underlined: "is this right?";
  * a page of buttons, with one swept: "what does this do?".
  * Scored: the answer is about the traced one.

*Prove:* the traced scenes right where the untraced ones guess. The look's
time and cost are no worse than without a trace.

### TF-07 · The answer lands on the traced thing — S — free

**Why:** "this one" answered with an arrow somewhere else is the 5 Oct
chess problem again.

**Do:**
* **Tora's ring and drawings** for a traced question go inside or beside
  the trace. A look action outside it, when a trace exists, is moved to
  the nearest control inside it (`look_region.best_control`), or dropped.
* **The card keeps off the traced area,** as it keeps off drawings
  (SN-09, SP-12).

*Prove:* a test: a look action outside the trace is pulled in. On screen,
the card never covers what was traced.

---

## Wave D — the trace in everything else

### TF-08 · "Reply to this", "write about this" — S — free

**Do:**
* **`write_for_me`:** with a trace, the page context is the traced text
  (TF-04), not the end of the page. "Reply to this message" over a chat
  bubble answers that bubble.
* **`reply_to_email`:** a traced message in a thread is the one answered,
  not just the newest.
* **`point_at` / guides:** "where's this in the menu?" with a trace over a
  thing means that thing.

### TF-09 · "This… and this" — M — free

**Why:** "compare this with this", "move this here". Two traces, and the
words say which is which by when they were drawn.

**Do:**
* **The strokes keep their times** within the hold (TF-01).
* **The voice line lists them in order,** each with when it was drawn
  ("(1) at 0.8 s, (2) at 2.4 s"), and the rule says that the first "this"
  is (1).
* **The look gets each crop,** labelled 1 and 2 in the image.
* **Later:** line the times up with the words (the live session's input
  transcription carries timing).

### TF-10 · The last trace stays for a follow-up — S — free

**Do:**
* **The last trace's focus is kept for 2 minutes,** in the same window.
  "And what about the one I showed you?" still has it.
* **A new trace replaces it.** A new window, or FS-01's new-screen words,
  clear it.

---

## Wave E — measured and shipped

### TF-11 · Every traced question measured — S — free

**Do:**
* **One line per traced question** in `tora.log`: `trace: 2 strokes
  (circle, underline), read 2 in 120 ms, line sent at release +0.08 s,
  look: crop 640x360 / none`.
* **The answer scorecard counts traced questions** answered without a
  look.

### TF-12 · A switch, and the release — S

**Do:**
* **Settings > General: "Show Tora with your mouse while holding the
  shortcut"**, on by default (decision 4).
* **The mist follows Tora's colour.**
* **Release** with the usual self-update check, after the owner has tried
  it live.

---

## Decisions for the owner

1. **The mist's look:** in Tora's colour, or a white-grey mist? And after
   the release, does the traced area keep a faint haze until she answers
   (recommended), or fade at once?
2. **When it draws:** only while the shortcut is physically held
   (recommended, as asked), or also while she listens hands-free after a
   tap?
3. **Pixels:** read the traced thing first and look only when it is a
   picture (recommended: fast, and no screenshot for text), or always look
   at the traced area?
4. **On by default,** with a switch to turn it off?

---

## The order, at a glance

| # | milestone | size | cost | needs |
|---|---|---|---|---|
| TF-01 | record the trace while held | S | free | — |
| TF-02 | the mist | M | free | SP-11/12 decided; decision 1, 2 |
| TF-03 | trail → what they meant | S | free | TF-01 |
| TF-04 | what is under it, read | M | free | TF-03 |
| TF-05 | the voice is told at the release | S | free | TF-04 |
| TF-06 | the look focuses on the trace | M | ~$0.05 runs | TF-03; decision 3 |
| TF-07 | the answer lands on the traced thing | S | free | TF-06 |
| TF-08 | reply/write about "this" | S | free | TF-04 |
| TF-09 | "this… and this" | M | free | TF-05 |
| TF-10 | the last trace stays | S | free | TF-05 |
| TF-11 | measured | S | free | TF-05 |
| TF-12 | switch and release | S | — | all; decision 4 |

**First useful cut:** TF-01 to TF-05. The mist, plus the voice knowing what
was traced, from text alone. Then TF-06 for pictures.
