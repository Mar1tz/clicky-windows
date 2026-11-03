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

The gear grows the shape to the Settings size. A light sidebar on the left
(13 px words, 15 px grey icons, 30 px rows, a soft plate while chosen -
`shape._NavRow`), thirteen rows in three groups, the version at its foot:

| Group | Rows |
|---|---|
| (top) | Account, General, Invite & Earn |
| **TORA** | Voice, Microphone, Shortcuts, Cursor, Style, Memory |
| **WORK** | Integrations, Agents, Routines, Skills |

Four pages sit **one step in**, with no row (`shape.INNER`), the row they
are under lit while they show: Plans (Upgrade, Account's Change plan),
Privacy and Advanced (rows at the foot of General), Code editors (a row in
Integrations). The owner, 29 Oct 2026, of sixteen rows: "very, very full
... it feels very squashed".

**Every settings page reads the same** (heyclicky2.0.mp4):

- A **title** (22 px) and **one grey line** under it (`shape.LEDES`).
- Small **caps labels** over each block (drawn in capitals by the font;
  the text stays as written).
- **Blocks** of `SURFACE`, rounded; in a block, **rows**: a 13 px title, a
  faint 12 px line, the control at the end. A row that opens something
  has a chevron (`Group.link`); a row that is one of a list to pick from
  has its round mark first and no chevron (`Group.choice`).
- Choices that are pictures are **tiles** (`ui/shape_widgets.py`): her
  cursor in each colour, glowing; each voice with its colour's waveform,
  name and Google's word for how it sounds. The chosen one has an accent
  ring and a tick.
- **Keys** are keycaps at the row's end, beside Change.
- **No row of long buttons:** a row each, with a short button (they need
  more width than the shape has in a wider font).

**Step 5a** (`ui/shape_settings.py`) split the app's tabs into these pages,
each made of parts the app already had - borrowed, not copied:

| Page | What is on it | From |
|---|---|---|
| Account | This month on the plan (Change plan), then profile, how Tora thinks, sign out, delete | Usage + Account tabs |
| Plans, Privacy, Invite & Earn | as they were (§5) | their tabs |
| General | Start with Windows, the floating panel, updates, theme | System + General tabs |
| Voice | Her voice (the carousel, 480 px here), her sound, how she answers | Tora page + General tab |
| Microphone | The microphone (Change), always listening | General tab |
| Shortcuts | Push to talk as keycaps (Change), show/hide her cursor | General tab |
| Cursor | Her colour, show her cursor, trail as a line | Tora page + General tab |
| Style | Her modes, instructions for Tora | Tora page |
| Memory, Agents, Code editors, Advanced | as they were | their tabs |
| Skills | Her skills, then record a lesson / show her what you did | Tora page |

Old names still asked for land on the new page (`shape.MOVED`: General ->
Shortcuts, Usage -> Account).

**Step 5b.** Two pages for what was only reachable by voice:

- **Integrations** (WORK): what is connected (Disconnect) and what can be
  (Connect) - Google's (Gmail, Calendar) and the MCP services in
  `integrations/catalog.py`, plus a custom connector. It calls the
  companion's own `connect_account` / `disconnect_account` tools, so the
  page and "Tora, connect Notion" behave the same. A sign-in opens in the
  browser and she says when it is done; a service with a key opens its key
  page and the page waits for **I've copied it** - the key is read off the
  clipboard once, kept in Credential Manager, wiped from the clipboard.
- **Routines** (WORK): the skills that run by themselves (their "Runs by
  itself" line), when each runs next and last ran, a switch and Edit each,
  and New routine (the skill editor).

Still waiting for something behind them: Dictation (a language, a
dictionary), a speech speed, a captions switch.

## 5. Plans

**Upgrade** (gone on Max, and on their own Gemini key) grows the shape into
Settings on **Plans**. Free, Pro and Max are three cards side by side with
room between them: who it is for, the name with its flag ("Pro [Popular]"),
the price "/mo", one line, the ticked allowances in credits, one button.
The current plan is outlined in the accent and its button says "Your
plan". Billing (renewal, credit packs, Manage billing) is under the cards.
The words and prices are ours (`settings_tabs.PLANS`, heytora-web
`site.ts`). No Monthly / Yearly switch: there are no yearly prices.

**Invite & Earn** is a settings page too (the foot's **Invite & earn**, or
the sidebar) - and **ours, not HeyClicky's**: the owner preferred our
referral design (29 Oct 2026). The offer in one line, then Refer (the gift
card, how it works, your link with Copy, months waiting), Past referrals
(each friend and how close they are) and Apply referral (a friend's code,
for a new account). It is `referral_window.ReferralPanel`, the same panel
the app window's referral window shows.

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
app wears that app's colours, kept in one map beside the picture and
nowhere else (heytora-web BRAND.md §2). (The old tour scenes that had
`DEPICTED` went in revamp step 6b.)

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

Two parts since revamp step 6b:

- **The sign-in window** (`ui/setup_wizard.py`): a centred card on the
  ribbon - Sign in via browser, or (signed in, or a build without
  accounts) your own Gemini key - and **Finish**, locked until signed in.
  The sign-in gate shows it before the overlay, hotkey and voice exist;
  finishing it does not mark the first launch done. Since step 6c it is
  drawn as the tour's card, so the two read as one thing.
- **The tour** (`ui/onboarding_shape.py`, `ui/onboarding_steps.py`,
  docs/ui/HEYCLICKY_ONBOARDING.md): grows out of the pill once the app is
  up - hello, her voice and colour, the mic, talking, the interview, circle,
  drive, typing, the jobs it makes ready, a plan. Its end marks the first
  launch done; the tray's Run again opens it.

The old wizard's pages (welcome, work, privacy, mic, ready, shortcut, try,
payoff, voice, personal, done - after Wispr Flow's five phases) and the
tour scenes on its welcome page were deleted in step 6b; the tour does
each of their jobs. What the tour keeps from them:

- **The hold-to-talk step borrows the hotkey** (`claim_hotkey`), so holding
  it lights the key without starting a conversation.
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

**Its glow** (29 Oct 2026, after HeyClicky's notch): while she thinks,
speaks or listens, a soft light at the pill's far end in our violet, amber
or teal (`tokens.STATE_GLOW`), drifting a little, brighter as the voice is
louder - the state at a glance, in colour.

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
| 3b | Typed questions: **Ask Tora…** beside Talk in Chat. Enter tucks the shape away (she reads their screen, not HeyTora's), then `CompanionManager.ask_typed` asks it as a spoken question is asked - it stops an answer under way, counts as a talk message, goes to the live session with the screen line (`RealtimeConversation.ask_text`), or the slow route without one or when the session never answers - and she answers aloud with her words on the card; it is kept in the journal. Only the history redraws, so a question half typed survives | Built; needs a live run |
| 3c | **Jobs** is the fourth tab (Home · Chat · Insights · Jobs); the foot's **New job +** opens it with the job box ready. Give Tora a job (a question is pointed to Chat) · Needs your OK (Approve / Deny, a sensitive one with its typed word) · Working now (step, what it is doing, progress, Pause / Resume, Stop) · History. Each part redraws only when what it lists changes, so progress never wipes what is being typed. A job's details (plan, log, folder) still open in the app window | Built; needs a live run |
| 4 | Plans as three cards, yours outlined, Upgrade gone on Max (§5); Invite & Earn as a settings page (§5) - the referral window is no longer opened from the shape | Built; needs a live run |
| 5a | Settings split into HeyClicky's pages from what exists (§4) | Built; needs a live run |
| 5b | Integrations and Routines pages (§4) | Built; needs a live run |
| 5c | Dictation, speech speed, captions - each needs its feature first | |
| 6 | Onboarding restyled (§10, docs/ui/HEYCLICKY_ONBOARDING.md) | Built |
| 6a | Everything in the shape: a third size, **page** (Settings' size, Back instead of the sidebar) for all of Insights (laid out narrow) and everything asked; a job's details slide in over the Jobs tab (the drawer covers the shape's body); the tray's Open HeyTora, starting HeyTora again, an agent's orb and every old page name (`app/...`, `invite`, `skills`) open the shape there (`Shape.go`). The shape owns its bridge. `app_widgets.later()` replaces bare `singleShot(lambda)` on pages - one ran after its page was gone and PyQt aborted | Built |
| 6b | The old app window (`ui/app_window.py`, its Home and Agents pages), the Settings dialog (`ui/settings_dialog.py`), the referral window, the old wizard's pages and its tour scenes deleted; `ui/setup_wizard.py` is the sign-in window and the starter-skill lists. Their tests moved onto what stays - the plan-tracking tests now run on the shape's Settings, the referral tests on the panel, a job's details on its drawer - or went with what they tested. Signing in no longer marks the first launch done: the tour's end does. The old Home's warning came back on the shape's Home: a strip under the greeting only while Tora can't answer (this month's credits used up - shown from the plan before a question is refused -, a refused or empty own key, our server, no internet, signed out), with **See plans** / **Fix it** / **Sign in** one click away (`AppBridge.trouble`) | Built |
| 6c | The sign-in window in the new design: the tour's card (no Windows frame, the app's ground, the tour's title and grey line, Finish at the foot), centred, as tall as what it shows, dragged by its background, a close X; signed in, a blue card says who and the plan; the own key waits behind one link. The old 1080 px window on the ribbon is gone, and with it the tick and cross the font couldn't draw | Built |
| 6d | The owner's walkthrough, closer to HeyClicky (31 Oct): **Account** is the plan card (which plan, the month, a bar per allowance, Upgrade) and Log out / Delete account / Quit - no name box, picture or "how Tora thinks". **General**: Show in taskbar (a HeyTora button in the taskbar, `ui/taskbar.py`), Launch at login, updates, theme, Replay the welcome tour; the floating panel's switch went (it flipped itself off). **Shortcuts**: Ctrl, Ctrl is always on. **Agents**: the folder and two switches; the limits, models and history one step in (Limits & models). **Insights** and everything asked open at their own size, 1060 x 700 (smaller on a small screen), so Insights is wide again. **Sounds**: a soft one as the shape opens and as it grows into Settings (Voice > Interface sounds) | Built |
| 6e | Second walkthrough (31 Oct): **one main button at a time** - the sign-in card shows Continue only once signed in, and the tour's last step drops Skip and the "Your plan" button (Start using HeyTora is the one way on). **Account**: the allowances stacked, a bar each, and **Your activity** - the old Insights' calendar (a square a day), the streak, three measured numbers, and All insights. **Insights is no longer a tab** (Home · Chat · Jobs); old links open the whole page. **Smoother grow into Settings**: the window is no bigger than open + Settings until a whole page is opened, Account reads the plan once and after the grow, the sounds load with the shape. **Sounds**: Settings has the chime; each shrink plays its opening sound backwards and lower | Built |

Each step: a screenshot first, then code, then this table updated.
