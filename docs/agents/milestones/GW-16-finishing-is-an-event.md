# GW-16 — finishing is an event

**Status: done.** 17 new tests, 917 passing. Live: after finishing, the
model answered questions about the walkthrough correctly 6 times in 6.

## The problem

A finished walkthrough ended on one spoken line, "That's the last step —
you're all done!", and then vanished:

* **Nothing on screen** said what had been done.
* **Nothing was kept.** The next time, the same goal was planned from
  scratch.
* **The live model never knew.** Steps are spoken in Tora's own voice, so
  "what was the second step again?", asked straight after the end, had
  nothing to answer from (the gap GW-15 measured, one step later).

## What was built

**The runner hands its ending to the app.** `GuideRunner(on_finish=...)`
is called once when the walkthrough completes, including when the user got
there ahead of it. The plain line is still said when there is no
`on_finish`, or when it fails, so an ending is never silent.

**The hub closes it** (`_guide_finished`):

| | |
|---|---|
| said | "That's it - all done! I've saved the steps to your journal." |
| shown | the same line, then **✓ Set up the invoice template · 4 steps · 1 min 42 s** |
| kept | a journal entry: *How do I set up the invoice template?* / the numbered steps, tagged `walkthrough` |
| told | for five minutes, the per-turn context says which walkthrough just finished and lists its steps |

**The journal keeps it like anything else Tora taught.** It shows up in
"what did I learn today", marked *(walkthrough)*, and it comes back for
spaced review. The plan itself is stored beside the entry in a new
`walkthroughs` table: the planner's own JSON, with the snapped targets
the walkthrough actually used. `guide_from_dict(guide_to_dict(plan))` is
exact, and `plan()` now parses through the same function, so a saved
walkthrough comes back exactly as a fresh plan would. Replaying it is
GW-21. Because it is a new table, an existing journal needs no migration.

**Journal off → nothing kept, nothing promised.** A journal that cannot
be written is logged and the ending goes on without "I've saved…".

## One change from the plan

The plan said the ending *offers* to save. It saves instead, and says so:

* The journal already keeps every answer Tora gives without asking. An
  exception for walkthroughs would be the odd one out, and the journal's
  existing on/off setting governs it.
* An offer needs a reply. The ending is spoken in Tora's local voice, so
  the live model never hears the question. A "yes" would reach it with no
  idea what it answers, and would need extra state to route.
* GW-21 needs every finished walkthrough kept anyway, to replay it.

If an explicit offer is wanted, it can go on top: the save is one call.

## The live check

The model was given the real per-turn context, 20 seconds after a
walkthrough finished (2 runs each):

```
ok 'what was the second step again?' -> "The second step was to choose Layout presets."
ok 'thanks!'                         -> "Your invoice template is now all set up and ready to go."
ok 'where did you save it?'          -> "I saved the steps in your Tora journal, so you can review them."
```

No `guide_step` calls. The first wording, "It is saved in their journal",
was read as the user's **work** being saved: "your new Classic B template
has been saved to your journal". The context now says the walkthrough's
**steps** are saved in their Tora journal, and so does the spoken line.

## Proof

`tests/agents/test_e2e_guide_finish.py` (17):

* the plan round-trips exactly;
* the journal entry reads as a question with numbered steps, and its
  stored plan becomes a Guide again;
* a goal already phrased "how do I…" is not doubled;
* "what did I learn today" marks walkthroughs;
* the summary line, four ways;
* the ending: said, shown and kept;
* getting there ahead is also a finish;
* journal off, and a journal that cannot be written;
* the runner hands its ending over, keeps its plain line without
  `on_finish`, and falls back to it when `on_finish` fails;
* the model is told what just finished, and not five minutes later.

## Seen, not changed

The panel's response area (`ui/panel.py`) appends every reply to the
last one without a separator, and nothing calls `clear_response`. Every
local reply behaves this way, not only this one. The caption island,
which is where the ending is read, is not affected.
