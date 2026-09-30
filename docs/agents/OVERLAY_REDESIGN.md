# Overlay redesign — status and decisions

The plan is the Claude Doc "HeyTora overlay redesign — island, cursor and
step chip"
(https://claude.ai/code/artifact/d5f5a5a1-f7e1-4f23-8e48-195b546b0a42): nine
milestones, OD-01 to OD-09, in four phases with three gates. That doc
belongs to another account and can no longer be edited from this one, so
progress and the decisions taken since are recorded here.

The new look is built behind the `new_overlay` switch (`TORA_NEW_OVERLAY`,
off by default). The classic look stays exactly as it is until Gate C.

## Status

| Milestone | State | Commit |
| --- | --- | --- |
| OD-01 Tokens and preview | done | 6d63c32 |
| OD-02 Quiet when idle | done | 859f159 |
| OD-03 The arrow | done | b82bcad |
| OD-04 Step chip words | done | 3fc5b53 |
| OD-05 Step chip placement | done | 3fc5b53 |
| OD-06 Island size (the fixed bar) | done | 8f18e1b |
| OD-07 Island states | done | the OD-07 commit |
| OD-08 Settings | next | |
| OD-09 Review and switch | waits on Gate C | |

Gate A (the preview plays every state; nothing redraws at rest) passed after
OD-02. Gate B (the tip lands on the pixel; the chip never covers it) passed
after OD-05. Gate C is the user's yes on the preview; then the new look
becomes the default.

OD-04 and OD-05 were committed from the editor together with the Teachers
pack's `extensions/teachers/classroom.py`, so they share 3fc5b53.

## Decisions since the plan

**The island is a fixed bar (2026-09-29).** The user disliked the pill
growing as Tora spoke. In the new look the island opens at its final size,
440 x 58 px (60% of the screen width on a small screen), and keeps that size
from the first word to the last and through every state. A short reply sits
in the middle; a second line pushes the first up and dims it; past two
lines the oldest rolls away. This replaces the plan's "width fits the words,
160-440 px; 36 px for one line, 58 for two".

**One bar for every state (OD-07).** Listening, thinking, speaking and a
walkthrough all use the same bar. Only the glyph on the left and the words
change:

| State | Glyph | Words |
| --- | --- | --- |
| Listening | three bars that follow your voice | "Listening" |
| Thinking | a turning ring | what Tora is doing ("Analysing your screen…"), or "Thinking…" |
| Speaking | three bars that move with her words | what she says |
| Walkthrough | a still dot, then the step badge "2/5" | the step, between her words |

When she stops talking, the last words stay 1.5 s. After that the bar either
slides up and away in 200 ms or, during a walkthrough, returns to the step.
When you start talking, her words go at once and the bar shows "Listening".
It slides in over 200 ms with no bounce.

**The card by the cursor is gone in the new look.** What Tora is doing moved
into the island; the step's action is on the chip beside the target. The
classic look keeps its card.

**Island position.** Top centre by default. The plan asks whether it should
go top right instead; OD-08 adds a setting (top centre, top right, off).

## Where the build differs from the plan

- OD-01's "no colour of its own" check covers the new look's painter
  (`ui/overlay_paint.py`) and the classic blue, which is now defined once.
  `ui/overlay.py` and `ui/caption.py` still hold the classic look's colours;
  they go when the classic look is removed after OD-09.
- The ring round the target uses the bounds that
  `CompanionManager._highlight` already sends, marked `"role": "target"`,
  rather than a new signal.
- In the new look the chip replaces the classic look's card and bubble, so
  only one label is ever drawn beside the cursor.
- The new look draws only while something moves. A waiting walkthrough's
  bar costs no frames.

## Seeing it

```
python -m ui.overlay_preview --style new            # plays every state on the real overlay
python -m ui.overlay_preview --style both --shots DIR   # PNGs on light, dark and busy backdrops
set TORA_NEW_OVERLAY=1 && python main.py            # the app in the new look
```

Tests: `tests/agents/test_e2e_overlay_design.py`, one section per milestone.
