# Skills instead of packs

**Written for:** the two owners, who decide, and whoever builds it (Claude or
Dylan).

**Status (6 Oct 2026): built.** SK-01 to SK-08 are in the code. The pack
folders themselves (`extensions/`, `agents/extensions/`,
`agents/ui/extensions.py`, `ui/packs_page.py`, the pack tests and the pack
docs) are unused and still to be deleted; nothing imports them.

## The decision (6 Oct 2026)

HeyTora is sold as **Tora, who works in all your other apps**. Packs work
against that. Packs are 15 industry mini-apps, about 20,000 lines under
`extensions/`. Each new kind of work means more
Python. Users can't add their own, and the app looks like a bundle of tools
instead of one assistant.

Both owners agreed to take packs out. What replaces them is **skills**.

## What a skill is

A skill is **a text file that says how you like something done**. Tora carries
it out with the tools she already has: the screen, the browser, files and apps.

```markdown
---
name: Send an invoice
when: the user wants to invoice or bill a client
apps: Xero (in the browser)
ask_before: sending, anything over R10,000
---

## Steps
1. Find the client in Xero; if they're new, ask for their email first.
2. One line per piece of work, with the hours and the rate below.
3. Due in 14 days. CC accounts@mybusiness.co.za.
4. Show me the total before sending.

## What to know
- Design R650/hour, copywriting R450/hour, rush jobs +25%.
- Sarah (Bloom Studio) is billed monthly, on the last Friday.
```

* **The top block** holds the few fields Tora needs in order to pick the skill
  and keep it safe:
  * `name`
  * `when`, in plain words. This is not a list of keywords.
  * `apps` (optional)
  * `ask_before` (optional)
* **The body is free text.** The user writes it in their own words: steps,
  prices, templates, people. No code, and no schema to learn.
* **A skill never adds new abilities.** A tool does that, in code, and rarely.
  A skill only adds know-how. That is why one person can write a skill in a
  minute and it can't break anything.

**Two layers, so the line stays clear:**

| Layer | Form | Who adds it | How often |
|---|---|---|---|
| Tools: click, type, browser, files, apps, terminal | Code in `agents/tools`, `agents/control` | Us | Rarely: only when Tora physically can't do something yet |
| **Skills** | Text files | Us (starter skills), users, Tora herself | All the time |

## Where skills live

* **`skills/` in the repo:** the starter skills. They ship with the app and
  can't be edited in place.
* **`%LOCALAPPDATA%\HeyTora\skills\`** (the data folder, `config.data_dir()`):
  the user's own skills, one `.md` file each, and `skills.json` (which are off).
  * Editing a starter skill saves a copy here with the same name, and the
    copy wins.
  * The user can delete their copy to get the original back.
* **Plain files, on purpose:**
  * The user can back them up.
  * The user can open them in Notepad.
  * Later, they can share them with someone else.

## How Tora picks a skill

1. **A short catalogue goes into the prompt.** It is one line per skill
   (`name: when`). There is one for Tora's voice and one for the agents'
   classifier (`agents/understanding`). Thirty skills come to about 600 tokens.
2. **The model picks the skill, or no skill.** "Invoice Sarah for last week"
   matches *Send an invoice*. "What's the weather" matches none, and Tora
   answers as she does today.
3. **Only the chosen skill's full text is loaded** into that task's prompt.
   This keeps the cost per message where it is now, which is the same rule as
   in `docs/SHOW_THE_NEXT_MOVE.md`.

The agents already have the hook for step 3. `AgentManager._extras_for`
(`agents/manager/agent_manager.py`) already puts a pack's `skills` text into
the planner's and worker's prompts. Skills replace where that text comes from,
not how it's used.

**The voice uses skills too.** "How do I send an invoice?" is a walkthrough on
the user's screen, and the same skill tells Tora which app and which steps.
"Invoice Sarah" is a background job.

## How a skill runs

A skill runs as an ordinary agent job, so it appears on the Agents page under
*Working now*. It goes through the same planner, workers, approvals and
history as any other job.

**How it acts on apps.** The agents already have a ladder and always use the
gentlest rung that works:

1. The app's command line or a deep link: no window needed.
2. Windows UI Automation (`agents/control/uia.py`): buttons and boxes are
   pressed in place, the window can be behind others, and the mouse is not
   touched.
3. The hidden browser (`agents/tools/browser_tools.py`): most office work is
   done here.
4. The real mouse and keyboard: only when nothing else reaches the app. This
   is visible, and the user is told before it starts.

So most skills run in the background while the user keeps working.

### The sign-in gap (real work, SK-06)

The hidden browser starts a fresh Chromium every time (`chromium.launch`), so
it is signed in to nothing. A skill that works in Xero or Gmail would stop at
the login page. The fix:

* **A HeyTora browser profile** in `%LOCALAPPDATA%\HeyTora\browser\`, using
  Playwright's persistent context. It is kept between jobs, on this PC only.
* **"Sign in once":**
  1. The first time a skill needs a site, Tora opens that browser *visibly*
     and says "sign in to Xero here, then close it".
  2. After that it runs hidden and is already signed in.
* **The existing rule still holds:** password fields are never filled
  automatically. They always ask a person.
* **Settings → Data & privacy:** a list of signed-in sites, each with
  *Sign out*, plus *Forget all*.

## Safety

These rules are not new; skills keep them:

* **The floor is in code and can't be turned off by a skill.** Paying,
  sending, posting and deleting for others to see always ask, even with
  *Approve automatically* on. This is the current rule in Settings → Agents.
* **`ask_before` adds to the floor, and never takes away from it.**
  * In v1 it goes into the task's prompt as a hard rule.
  * In v2 it is enforced in the approval gate as well. For example, an
    amount over a limit makes the step ask.
* **The user's risk ceiling** (*Run without asking, up to…*) applies to
  skill jobs exactly as it does to any other job.

## Writing skills: the Skills page

This goes on the Tora page, as a tab beside *Teach her*: it's about her, not
about a separate app.

* **A list of skills:**
  * the starter skills
  * the user's own, marked *Yours*
  * an on/off switch for each
  * when each one last ran
* **New skill:** a dialog with a name, "when to use it", and one large box for
  the steps and the context. That's all.
* **"Tora, remember how I do this":**
  1. After a job or a walkthrough, or when asked by voice, Tora drafts a skill
     from what just happened.
  2. The user reads it, edits it if they want, and saves it. Nothing is saved
     without that look.
* **Each skill's history:** what it did, and when. It is the job history
  filtered to that skill, shown on the skill.

## Starter skills

We don't port the packs. We write about 15–20 short skills for the most
common office tasks:

* send an invoice
* chase an unpaid invoice
* file receipts into a spreadsheet
* book a meeting
* draft a reply in my tone
* turn a voice note into an email
* weekly summary of my inbox
* tidy my Downloads folder
* rename and file documents
* make a social post from a link
* write up meeting notes
* make a simple quote
* compare prices for something I want to buy
* fill a form from a document
* month-end checklist

## Scheduled skills

Packs could run on a schedule or when files appeared, through
`agents/manager/scheduler.py` and the `Schedule` / `FolderWatch` triggers in
`agents/extensions/hooks.py`. That is worth keeping:

* An optional `every:` field in a skill, for example `every: Friday 16:00`, or
  `every: new file in Downloads\Receipts`.
* **The scheduler stays.** Its two trigger shapes move out of
  `agents/extensions` before that folder goes.

## The order of work

| # | Milestone | What it does | Done when |
|---|---|---|---|
| SK-01 | Skill files | The format, the loader for both folders, a user copy winning over a starter skill. A bad file is skipped with a reason, never a crash. | Tests: parse, override, broken file |
| SK-02 | Picking and running | The catalogue goes into the classifier and the voice prompt. The chosen skill's text goes into the job (`_extras_for`). `ask_before` goes into the prompt. | A job asked in the skill's words gets its steps; an unrelated question gets none; the cost per message is unchanged |
| SK-03 | Starter skills | The 15–20 files above. | Each one checked once by hand on a real PC |
| SK-04 | Skills page | The list, New, Edit, the switches, the history. | Covered by the UI tests, like the other pages |
| SK-05 | "Remember how I do this" | Tora drafts a skill from a job or a walkthrough, and the user reviews it before it's saved. | A draft is never saved without the user |
| SK-06 | Browser sign-ins | The persistent profile, "Sign in once", and the list under Data & privacy. | A second job on the same site needs no sign-in |
| SK-07 | Remove packs | Covered in the next section. | `python -m pytest tests/agents -q` green; the app starts with no `extensions/` folder |
| SK-08 | Scheduled skills | `every:` on top of the scheduler. | A weekly skill shows on the Agents page and runs |

**SK-07 lands only after SK-02**, so there is never a build in which the
agents lose domain guidance and nothing has replaced it.

## What SK-07 removes

Found by searching the code for every user of packs:

* **The packs:** `extensions/`, all 15 packs.
* **The framework:**
  * `agents/extensions/`, after moving the scheduler's trigger shapes out
  * `agents/ui/extensions.py`
  * the pack hooks in `agents/bootstrap.py`, `agents/manager/agent_manager.py`
    (`self.extensions`), `agents/workers/base.py`,
    `agents/verification/verifier.py` and `agents/tools/design_tools.py`
* **The app:**
  * `ui/packs_page.py`
  * the Packs rows in the sidebar (`ui/app_window.py`, `ui/app_pages.py`)
  * the pack calls in `ui/app_bridge.py`
  * the pack menu in `ui/tray.py`
  * the pack style rules in `ui/app_theme.py`
  * "Industry packs by voice" in `companion_manager.py` (`_manage_extensions`
    and the `list_extensions` / `enable_extension` actions)
  * the pack settings in `config.py`
  * the pack mentions in `ai/gemini_realtime.py`
* **Tests:** about 18 files under `tests/agents/` about packs or the
  framework. Any that test something that stays, such as the scheduler, move
  to its own tests.
* **Docs:**
  * remove `EXTENSIONS.md`, `PACKS.md`, `ACCOUNTING.md`, `FREELANCER.md`,
    `CREATOR.md`, `MARKETING.md` and `STUDENTS.md` from `docs/agents/`
  * update `ARCHITECTURE.md`, `docs/ui/DESIGN.md` §3G, and `CLAUDE.md` (the
    *pack owner* row in the ownership map, and the `extensions/` line)

## Open questions for the owners

1. **Sharing skills.** Should a user be able to send someone a skill, or should
   there later be a small shared library? It isn't needed for v1, but plain
   files keep the door open.
2. **Starter skills: on by default, or picked at first launch?** Onboarding
   already asks what HeyTora is for, so that answer could switch on the
   matching starter skills.
3. **Connections (APIs/MCP).** These are not part of this plan. If one app
   later proves too slow or fragile through the browser, a connection to its
   API becomes a new *tool*, and skills can use it when it's there.
