# Video creator pack

*The third industry pack, and the first built on the shared kit
(`extensions/packkit.py`). Code: `extensions/creator/`. Tests:
`tests/agents/test_creator_pack.py`, offline and with no model.*

This pack is a producer for a one-person channel. It covers ideas,
scripts timed at the creator's pace, shot lists, chapters, upload details
checked against YouTube's rules, sponsor deals and the week's numbers.

## What it does without a model

The mechanical parts of making a video are code, not prompts:

* **Script length** is worked out at the creator's speaking pace (`wpm`
  setting, default 150).
* **Shot list** (CSV): one A-roll row per spoken paragraph with its
  seconds, and one B-roll row per `[B-roll: ...]` or `[Shot: ...]` line.
* **Chapters** come from the script's `## headings`. A section under 10
  seconds never gets its own chapter. A short hook at the start takes the
  name of the first real section, still at 0:00.
* **Upload details** are checked against YouTube's rules:
  * title up to 100 characters;
  * description up to 5000 characters;
  * no `<` or `>`;
  * tags up to 500 characters in all;
  * chapters start at 0:00, number at least three, and each lasts 10
    seconds or more.

  Problems come back as findings. The file is still written, so it can be
  fixed in place.
* **Channel numbers** come from a YouTube Studio *Content* export (CSV). The
  Total row is skipped and numbers may contain commas. A video with 1,000+
  impressions and less than half the average click-through is flagged.

The model writes the script, the title and the hook, and the pack keeps
them.

## Levels

Every tool is READ or DRAFT. Nothing is uploaded, published or sent to a
brand. Publishing becomes a POST tool once a platform connection exists
(catalogue gate 2).

## Workflows

* **Weekly channel review** runs on Mondays at 09:00 when `auto_weekly_review`
  is on. It covers the pipeline, what slipped, deals late or due within a
  week, and the numbers.
* **Prepare an upload** builds the chapters from the script, then the model
  writes the title, description and tags and saves them through the check.

## Try it

Say "run my weekly channel review", "add a video idea about kerning",
"prep the upload details for Logo reveal" or "log a sponsor deal with
Canva". The dashboard opens from the Extensions card.

## The kit

`extensions/packkit.py` holds what every pack after Accounting needs:

* `Level`, the four action levels;
* `ToolBook` and `PackTool`, where a tool's approval flags come from its level;
* `Store`, a folder with settings, one SQLite database and sub-folders;
* `PackWorker`, where a failed check becomes a note and the checklist carries on;
* `rule()`, where a request needs a verb and an object;
* `PackExtension`, the whole pack from class attributes.

`extensions/packkit_ui.py` draws any pack's dashboard from its
`snapshot()`, so a pack writes no Qt at all. Both are modules, not
packages, so pack discovery skips them. `tests/agents/test_e2e_every_pack.py`
holds every pack to the same load, level, worker, routing and dashboard
checks.
