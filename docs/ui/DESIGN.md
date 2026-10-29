# HeyTora app design

The rules for every surface of the Windows app. Read this before changing
anything under `ui/` or `agents/ui/`. If a change doesn't fit these rules,
change this document first, in its own commit, and say why. Without that,
we end up redesigning the same screens over and over.

The values live in `ui/tokens.py`; the stylesheet in `ui/app_theme.py`.
The website's equivalents are in `heytora-web/src/styles/globals.css`.
What we took from HeyClicky, page by page with screenshots, is in
[HEYCLICKY_REFERENCE.md](HEYCLICKY_REFERENCE.md).

---

## 1. The idea

> **HeyTora lives in one place: the pill. Click it and it grows into the
> app; close it and it shrinks back. There is no separate app window.**

- **The structure is HeyClicky's.** One shape that grows to the size the
  job needs, tabs along its top, settings in a sidebar inside it, plans as
  a card over it. They are the closest product to ours, and their pages
  show more of what matters with less.
- **The look is ours.** Our colours and our accent blue, Plus Jakarta
  Sans, and light and dark mode following Settings → Appearance. Nothing of
  HeyClicky's name, mascot, wording or prices.
- **It moves.** The pill can be dragged to any side of any screen (as the
  flow bar can today), and the app grows from there towards the middle of
  the screen. HeyClicky's notch is fixed at the top. Ours goes where the
  person wants it.

## 2. The three sizes

| Size | What it is | About |
|---|---|---|
| **Pill** | At rest on its edge. Shows what Tora is doing: listening bars, thinking dots, speaking (§7A). A soft glow while an agent job runs | 46 × 10 at rest |
| **Open** | Tabs and the page. What you see when you click the pill | 520 × 420 |
| **Settings** | The same shape, grown, with the settings sidebar | 820 × 660 |

Plus one card that sits **over** the shape: **Plans** (from Upgrade,
anywhere).

**How it grows.** The pill's black shape grows out from the edge, then the
content fades in and settles:

| Time | What happens |
|---|---|
| first frame | The pill, exactly: the clock starts only once the window is on the screen |
| 0–140 ms | The shape grows from the pill to the full size, on an ease-out (`shape.GROW`; the site's curve did 60% in the first 30 ms and read as a pop) |
| 90–250 ms | The page fades in and lifts 6 px into place, while the shape is finishing - never an empty box after it |
| change of size | The old page fades out as a picture while the shape grows (first 40%), then the new one fades in |
| close (180 ms) | The page fades in the first 40 ms; only then does the shape shrink back into the pill |

Open → Settings grows the same way, from the open size. Nothing slides
across the screen and nothing bounces. With Windows' animation effects off,
it appears at once.

**The window never moves while the shape does.** It is made big enough for
the pill, open and Settings when it opens, and stays put until it closes;
what moves is painted inside it. A Windows window moved or resized
mid-animation shows one frame of its old picture in the new place (found
frame by frame in a screen recording, 28 Oct 2026).

**It opens towards the middle of the screen.** Docked at the top it grows
down; at the bottom, up; on the left, rightwards; on the right, leftwards.
Its centre stays level with the pill, and it is moved back inside the
screen if it would run off it.

**It closes** with Escape, a click outside, the pill again, or the tray.
Closing never stops a job.

**Light and dark.** At rest the pill is dark in both themes: it sits over
unknown content, and dark reads over anything. As it grows it takes the
theme's colours (`T.BG`, `T.SURFACE`): white and soft grey in light, black
in dark.

The tray icon stays, because people on Windows expect one. Left-click
opens the shape; the right-click menu is short (open, pause, quit).

## 3. Open: the tabs

```
 [Home][Chat][Insights]                     [Upgrade][⚙]
                 Evening, Dylan.
                 1 job working
        ┌──────────────────────────────┐
        │  the newest result           │      ▢ Working · <job>   ← beside the shape
        └──────────────────────────────┘
 [Invite & earn]                                 [Jobs +]
```

- **Top bar:** the tabs on the left as small icon buttons (the chosen one
  on a `NAV_PLATE`). On the right, **Upgrade** (a primary pill; on Max it
  says nothing and is gone) and the **gear** (Settings).
- **Home:** the greeting with the person's name and one line of live
  status ("1 job working", "Ready"). Then **one** large card: the newest
  finished thing (a job's result with its preview and one button, or the
  last answer). With nothing yet, it teaches: the shortcut as a keycap and
  three things to try asking.
- **Chat:** everything asked, by voice or typed, newest at the bottom.
  At the foot, a big **Hold [keys] to talk** button and an **Ask Tora…**
  box side by side. Empty, it says in one line what will be here.
- **Insights:** our Insights page (time Tora worked for you, things she
  handled, what she knows, streak, usage, agents), in the shape's width.
  The journal (everything asked, reviews) is reached from here.
- **Footer:** **Invite & earn** on the left, **Jobs +** on the right (the
  agents board: running, needs your OK, history).
- **Running jobs** sit as small cards beside the shape, on the side away
  from the edge: "Working", the job's name, a thin progress line. They are
  there whenever the shape is open; clicking one opens it in Jobs.

## 4. Settings

The gear grows the shape to the Settings size. A sidebar on the left in
three groups, the version at its foot:

| Group | Pages |
|---|---|
| (top) | Account, General, Invite & Earn |
| **TORA** | Voice, Microphone, Dictation, Shortcuts, Cursor, Memory |
| **WORK** | Integrations, Agents, Routines, Skills, Code editors |

Privacy is a section of General. Back (top left) or Escape returns to the
open size.

**Every settings page reads the same:**

- A **title** (`Page title`, §5) and **one grey line** under it saying
  what the page is for.
- Sections with a small **CAPS label** above a rounded `SURFACE` block.
- In a block, **rows**: a title, at most one grey line under it, and the
  control on the right (switch, button, chips, keycaps). Rows are split by
  hairlines. Nothing in a block is another block.
- Choices that are pictures are **tiles** (voices, cursor colours,
  languages): the chosen one has an accent ring and a tick.
- Longer notes go in one small grey line under the block.

What each page holds is in HEYCLICKY_REFERENCE.md §4, mapped to the tabs it
replaces (`ui/settings_tabs.py`).

**For now** (step 2) the sidebar holds the app's existing tabs, regrouped:
Account, Usage, Plans, General (the System tab), Privacy, Invite & Earn ·
TORA: Shortcut & mic, Memory · WORK: Agents, Code editors, Advanced.
Step 5 splits and rebuilds them into the pages above.

## 5. Plans

A card over the shape, from **Upgrade** or Account → Upgrade. A Monthly /
Yearly switch, then Free, Pro and Max side by side: name, price, one line
of who it is for, three ticked allowances in credits, one button. The
current plan is marked. The words and prices are ours (`settings_tabs.PLANS`,
heytora-web `site.ts`).

## 6. Where the values live

Every colour, radius, shadow and button size is a name in **`ui/tokens.py`**;
**`ui/kit.py`** is how they are drawn (the tween, colour mix, font, and the
one soft shadow). A UI file never writes a colour, a radius or a shadow as a
number - `tests/agents/test_e2e_design_tokens.py` fails if it does. To change
the look, change a token; to add one, add it to tokens.py and this section
together.

**The three blues, one job each.**

| Token | Hex | Its job, and only this |
|---|---|---|
| `BLUE` | `#3380FF` | The accent: primary buttons, switches on, focus, selection, links, progress, the "active" dot. Tora's cursor by default |
| `LOGO_BLUE` | `#00B6FD` | The wordmark's swoosh, and the first stop of the ribbon |
| `ICON_BLUE` | `#009EFD` | The app icon (`assets/brand/heytora-icon.svg`); lives in that file |

`BLUE_TINT` / `BLUE_TINT_DARK` fill a chosen chip or tile; `ON_COLOUR`
(white) is type and glyphs on a blue or a deep gradient.

**Frame vs paper.** The *frame* (the shape, its pages, cards and controls)
follows light/dark through `app_theme` (`T.BG`, `T.SURFACE`, `T.TEXT`...).
*Paper* is content the app draws - the equation card, the peeker's card,
skill cards on a gradient, the windows in the tour scenes - and stays light
in both themes (`PAPER.card`, `.ink`, `.dim`, `.faint`, `.line`,
`.edge`...). `STAGE` is the one dark moment in the light flow ("Nice job.").

**Depicted apps keep their own colours.** A picture of someone else's
app - the tour's music player, spreadsheet, PDF, album covers, the
florist's site - wears that app's colours, and they live in one map,
`tour_scenes.DEPICTED`, and nowhere else (heytora-web BRAND.md §2).

**Scales.**

| | Values | Use |
|---|---|---|
| Radius | `RADIUS_SM` 8 · `MD` 12 · `LG` 16 · `XL` 20 · `pill(h)` | inputs and cells · rows' blocks, chips, tiles · the shape, floating cards · the one card a screen is about · buttons |
| Button height | `BUTTON_H` sm 30 · md 34 · lg 44 | inside a row · the app · a screen's one action |
| Shadow | `SHADOW` card · float · hero | a drawn card on colour · the shape, popups, pickers · sign-in, You're set |

A pill's radius is `pill(h)` = one under half the height: Qt drops a
radius above half and the button goes square.

The families (`CAPABILITY`, `STEP`, `WORK_TILES`, `SCENE_LOOKS`,
`VOICE_COLOURS`) and the ribbon (`RIBBON`, `RIBBON_POOLS`, `RIBBON_FILLED`)
are in tokens.py too, copied from heytora-web.

## 7. Colour

### Frame
Neutral only: `T.BG`, `T.SURFACE`, `T.BORDER`, `T.TEXT*`. The single accent
is the **cursor blue `#3380FF`**. It is used for:

- primary buttons (Upgrade, Hold to talk, a page's one action)
- switches that are on
- the selected tab and sidebar row's icon
- chosen tiles and chips (ring and tick)
- focus rings, links, progress, the "active" dot

Nothing else in the frame carries a hue.

Status colours (`OK`, `WARN`, `DANGER`) are for status only: a failed task,
a used-up plan, Delete account. They are never used for decoration.

### Content: the site's gradient families
Copied from heytora-web, and moved into `ui/tokens.py` as named values when
first used. Same names, same hexes, so the app and site stay in step.

**Capabilities** (`home/what-gradient.tsx`): one family each, used wherever
that capability is the subject:

| Capability | Wash | Blobs |
|---|---|---|
| Ask | `#D7EBFF` | `#7CCBFF` `#00B6FD` `#1E7FE8` `#8FE3F0` `#E3B0FF` |
| Show me | `#D5DDFF` | `#2B63D6` `#6F7CF5` `#4F86E8` `#A48CFF` `#CFE9FF` |
| Teach me | `#F1E0FA` | `#B89CFF` `#F0A6FF` `#E48AD8` `#FFC6DE` `#D9D2FF` |
| Do it | `#D6F4F0` | `#7FB6FF` `#39D5C6` `#2FB8C8` `#8FE8D6` `#BDF2E6` |
| Build it | `#FBF3DD` | `#9CE9DA` `#FFE08A` `#F6B96B` `#FFF0C4` `#B6EE7A` |

**Work tiles** (`WORK_TILES`, from the site's `home/packs.tsx`): a two-stop
145° gradient icon tile with a white glyph, on skill cards:

| Icon | From → to |
|---|---|
| ledger | `#00B6FD` → `#3D7BFF` |
| briefcase | `#FFA24C` → `#FF6B6B` |
| spanner | `#F7CE5B` → `#F29B38` |
| banknote | `#39D5C6` → `#1FA88F` |
| calendar | `#A99BFF` → `#6F5BF5` |
| house | `#FF9DBC` → `#F26A8D` |
| chart | `#5B8CFF` → `#1E4FD6` |
| bag | `#9BE37A` → `#3FB87F` |
| wallet | `#FFC27A` → `#F28C3A` |
| video | `#FF6B6B` → `#E0314B` |
| mic | `#C9A0FF` → `#8C5BF5` |
| cap | `#7CC4FF` → `#00A3E8` |
| code | `#4A505B` → `#1F2329` |

**Steps** (`home/how.tsx`): onboarding uses these, one per step, in order:

| Step | Gradient |
|---|---|
| 1 | `#00B6FD` `#3D7BFF` `#7FE3D0` `#BDEBFF` |
| 2 | `#FF9A3C` `#FFC08A` `#DDF08A` `#FFE3C4` |
| 3 | `#8C7CFF` `#E9A2FF` `#FFC6DE` `#D9D2FF` |
| 4 | `#2FD0C0` `#B6EE7A` `#7CC8FF` `#D6F7EE` |

### Rules for content colour
1. **A thing keeps its colour.** A skill or capability has the same colour
   everywhere it appears. Never re-pick per screen.
2. **Gradients fill a whole surface:** the Home result card, an icon tile,
   an onboarding visual. Never a gradient border, gradient text, or a
   tinted row.
3. **Type on a gradient** is near-black (`#292929`) on the pale families and
   white only on the deep ones (work tiles). Check contrast. White on the
   site's `#00B6FD` fails.
4. **At most one large gradient per screen.** Small tiles can repeat.
5. Gradients look the same in light and dark: they are content, not frame.

## 8. Type

Plus Jakarta Sans everywhere (bundled, `assets/fonts`, SIL OFL), unhinted
with +0.3px tracking at body sizes (`tokens.app_font()`). Bold gets bolder
as type grows. Labels are the only uppercase, the only positive tracking.

| Tier | Size | Weight | Tracking |
|---|---|---|---|
| Greeting (Home) | 22 | 700 | -0.3px |
| Page title (Settings) | 22 | 700 | -0.3px |
| Card / row title | 14 | 600 | -0.1px |
| Body, the grey line | 13 / 12 | 400 | +0.3px |
| Label (uppercase) | 10 | 600 | +1.5px |
| Metric | 28 | 700 | -0.4px |
| Onboarding title | 30 | 700 | -0.5px |

The shape is smaller than the old window, so the titles are smaller too.

Sentence case with full stops in copy ("Never leave the screen."), as on the
site. No exclamation marks. No emoji in UI text.

## 9. Components

- **Buttons** are full pills. One primary (blue) per view; the rest are
  outline pills. Destructive = red text or red outline, never red fill.
- **Switches** slide on the site's curve.
- **Blocks** (settings sections, the result card): `SURFACE`, `RADIUS_MD`,
  no outline in light, a hairline in dark. Shadows are for the shape and
  what floats.
- **Keycaps:** a plate with a 2px side hairline. On Shortcuts, on Home's
  empty state, on the Hold to talk button.
- **Tiles:** a square-ish `SURFACE` cell, icon over name over a two-word
  feel; chosen = accent ring and a tick in the corner.
- **Chips:** pill-shaped, `SURFACE`; chosen = `BLUE_TINT` fill.
- **Icons:** Lucide outlines (`assets/icons`, ISC), 18px in the sidebar and
  top bar, 16px in rows. A new icon is a file there plus a line in
  `app_widgets.ICONS`.
- **Motion** uses `tokens.ease()` = the site's `cubic-bezier(0.22, 1, 0.36,
  1)`: 160 ms for small state changes, 250 ms for the shape (§2) and page
  changes inside it (a cross-fade, no slide). Respect Windows' "animation
  effects" off.
- **The listening pop** (`ui/sounds.py`): a short sound the moment she
  listens; Bubble, Bloop, Select, Spark, Chime or Off, chosen on Voice.

## 10. Onboarding

Built: `ui/setup_wizard.py`, `ui/onboarding_parts.py`. It keeps its flow
and is restyled to the settings page pattern (§4): step dots and **Skip**
at the top, one question as the title, one grey line, the live test, a
picker under it, **Continue**.

The steps prove what Tora is: she hears you, the key works, she sees and
points, she explains and draws. Q = a question beside a picture, T = a
centred test.

| Phase | Page id | Screen | Layout | What proves it |
|---|---|---|---|---|
| Sign up | `connect` | Sign in to HeyTora: a centred card with the logo, on the ribbon | T | Signed in. Continue is locked until then |
| | `welcome` | Hi, I'm Tora. Picture: the website's five "What it does" scenes, moving (`ui/tour_scenes.py`) | Q | Get started |
| | `work` | Tell us about yourself: name, and what you do (chips). Matching skill cards slide in, in colour | Q | A chip |
| Permissions | `privacy` | We look when you ask. Not before. The one real control: **Skip private windows** (`privacy_guard`) | Q | A choice |
| | `mic` | First, can I hear you? Live bars, what it heard, the mic picker | T | [Change microphone] [Yes] |
| Set up | `ready` | Getting Tora ready (only when opened by the sign-in gate) | T | The app has started |
| | `shortcut` | Test your shortcut: "Does the key light up while you hold it?" | T | The key lit at least once |
| Learn | `try` | Ask Tora about this: a worked sum with one wrong line | T | Tora pointed (timed). Skip |
| | `payoff` | Nice job (dark): only what was measured. Left out if nothing was | T | None, a moment |
| Personalize | `voice` | Pick her voice: one at a time, big, its waveform moving with the real clip | T | A choice |
| | `personal` | Make her yours: her colour, start with Windows, always listening | T | Choices |
| | `done` | You're set: the hotkey keycap, and **Open HeyTora**, which grows the pill open for the first time | T | Opens the shape on Home |

Rules that stay:

- Continue is disabled until the step is answered or proven.
- Back on every screen after the first. Skip only on Learn screens.
- Every Learn demo uses the **real** overlay and voice, not a recording,
  and counts against the plan like any other question.
- **The words hide while the shortcut is held** on a Learn screen, so Tora
  looks at the sum instead of reading the instruction out.
- **The shortcut test borrows the hotkey** (`claim_hotkey`), so holding it
  lights the key without starting a conversation.
- **Two halves.** The sign-in gate shows the window before the overlay,
  hotkey and voice exist. Leaving `mic` emits `ready`; `main.py` calls
  `attach(live)` on the same window.
- **Voices** are recorded once by whoever releases
  (`assets/voices/make_voices.py`) and ship with the app: choosing a voice
  never spends a talk message.
- **Drawing after a screen look:** for 20 s after look_at_screen highlights
  something, coordinate drawings are skipped; anchored ones still draw
  (companion_manager `_tool_draw`).

## 11. The pill

Built as the flow bar (`ui/flow_bar.py`, wired in `main.py`): resting pill;
bars while listening (✕ and ✓ when hands-free); faint dots and a turning
circle while thinking; her voice in the bars and the answer card while she
speaks. Dragged, it snaps to the nearest side of any monitor and stays there
(`TORA_FLOW_DOCK`). Two small lights sit off its ends: red while a
microphone stream is open, blue for two seconds after the screen was looked
at.

What changes: a **click** on the pill (not a drag) grows it into the shape
(§2). The answer card stays as it is, a fixed-size card that doesn't grow
as she talks.

## 12. What we don't do

- A separate app window, a separate settings window, a separate referral
  window. Everything is the shape.
- Colour in the frame beyond the accent. Gradient text or borders.
- More than one large gradient per screen.
- Surfaces that grow while Tora talks.
- Emoji, exclamation marks, "unlock", "supercharge", "seamless".
- macOS window chrome or traffic-light dots (heytora-web BRAND.md §1).
- HeyClicky's name, mascot, sample content, wording or prices.
- A new colour, font or radius that isn't in `ui/tokens.py`. If one is
  needed, add it there and here, together.

## 13. Order of work

| Step | Scope | Status |
|---|---|---|
| 0 | This document | Done |
| 1 | The shape: a click on the pill grows it open (§2) - `ui/shape.py`, `ui/shape_pages.py`. Home is its own; the app window's pages need a 1040 px column and can't be squeezed into 520, so Insights, Jobs, Settings, Upgrade and Invite & earn open the app window until each is rebuilt | Built; needs a live run |
| 2 | Settings inside it: the gear (and Upgrade, on Plans) grows the shape to 820 × 660; the sidebar holds the app's ten settings tabs, regrouped (`shape.SETTINGS`), each titled as its row; Escape steps back a size; Invite & Earn still opens the referral window. The tray's Settings still opens the app window's | Built; needs a live run |
| 3 | Tabs Home · Chat · Insights (`ui/shape_pages.py`): Chat is everything asked, newest at the bottom, and **Talk to Tora** (the shape steps aside, then she listens hands-free); Insights is the four measured numbers and this month on the plan, "All of Insights" opening the app window's page; up to three running-job cards beside the open shape, on the side away from the pill. Not yet: a typed **Ask** box - the app has no typed way into a conversation, so it needs work in companion_manager first | Built; needs a live run |
| 3b | Typed questions: an Ask box in Chat, and the conversation taking text as well as voice | |
| 3c | Jobs inside the shape (running, needs your OK, history), from **Jobs +** | |
| 4 | The Plans card; Invite & Earn as a settings page | |
| 5 | New pages: Voice (speed, voice tiles), Dictation (language, dictionary), Routines | |
| 6 | Onboarding restyled (§10); the old app window, settings window and referral window deleted | |

Each step: a screenshot first, then code, then this table updated.
