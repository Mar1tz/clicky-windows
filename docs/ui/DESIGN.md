# HeyTora app design

The rules for every surface of the Windows app. Read this before changing
anything under `ui/` or `agents/ui/`. If a change doesn't fit these rules,
change this document first, in its own commit, and say why. Without that,
we end up redesigning the same screens over and over.

The values live in `ui/tokens.py`; the stylesheet in `ui/app_theme.py`.
The website's equivalents are in `heytora-web/src/styles/globals.css` and
`heytora-web/src/components/home/*.tsx`.

---

## 1. The idea in one line

> **Raycast's restraint in the window, the website's colour in the content,
> Wispr Flow's onboarding as the model for ours.**

- **Frame (Raycast).** The window, sidebar, page headers, rows, buttons and
  inputs stay plain: neutral, quiet, dense enough to scan. Most good Windows
  apps look like this, and the window has to sit beside other apps for hours
  without tiring the eye.
- **Content (heytora.com, after Calendly).** Colour belongs to *things*: a
  capability, a pack, an onboarding step. Each gets a gradient from the
  site's own families (§4) and keeps it everywhere it appears. Colour is
  never decoration on the frame.
- **Onboarding (Wispr Flow).** A phase bar, questions beside a picture,
  and tests you pass by doing the real thing. §7.

If a screen has colour in its frame or a flat grey content card where a
pack or capability is the subject, it is off-brief.

## 2. Two kinds of surface

| | **Solid** | **Glass** |
|---|---|---|
| What | A window you work in | Anything floating over the desktop |
| Where | App window, onboarding, sign-in, dialogs | Floating panel, the pill and its result card (§7A), captions, cursor speech bubble, tray popup (the right-click menu on the taskbar icon) |
| Theme | Follows Settings → Appearance (light = site's soft grey, dark = site's black) | Always dark glass, in both themes. It sits over unknown content, and dark glass reads over anything |
| Recipe | `T.BG` ground, `T.SURFACE` cards, hairline `T.BORDER` | Fill `rgba(18, 18, 22, 235)`, 1px edge `rgba(255, 255, 255, 0.10)`, radius 16 (14 for the tray popup), soft shadow. This is the floating panel's look today (`ui/panel.py` paintEvent) |

Glass never appears inside the app window: no translucent cards, no blurred
sidebars. Real Windows blur (DWM acrylic) is a possible later upgrade to the
glass recipe. It is not a reason to put glass anywhere new.

## 3. Layout of a page

- **Header: the title and nothing else.** 26px bold on the left, the page's
  actions on the right. **No description line.** If a page needs explaining,
  the explanation belongs in its empty state, where it helps the person who
  needs it. Repeated on every visit, it's noise.
- **Sidebar:** see §3B. Selected row: a neutral `NAV_PLATE`, icon in the
  accent blue.
- **Content:** one column of cards, 32px gutter, 16px between cards. Lists are
  rows divided by hairlines inside a card, not a card per row.
- **Empty states** are written, not blank: what this page will show, and the
  one thing to say or click to get there.
- **Window title:** "HeyTora". Never the page name plus the app name. Qt
  appends `applicationDisplayName` to every window title, so set the title
  once.

## 3B. The shell and the page map (after Wispr Flow)

Built in `ui/app_window.py` (layout), `ui/app_shell.py` (painted rows and
title bar buttons), `ui/app_popover.py` (the cards), `ui/window_frame.py`
(our own title bar on a real Windows window).

```
┌─[▥][◯]──────────────────────────── [●Listening] [🔔] ─ ▢ ✕ ┐
│ heytora      ╭───────────────────────────────────────────╮ │
│ ⌂ Home       │ Hey Dylan, just hold [Ctrl + Win] and ask │ │
│ ◇ Agents     │                                           │ │
│ ▢ Journal    │ …the page…                                │ │
│ ▦ Packs      │                                           │ │
│   Accounting │   (only while the Accounting pack is on)  │ │
│ ✦ Tora       │                                           │ │
│ 🎁 Free month │                                           │ │
│ ⚙ Settings   │                                           │ │
│ ? Help       ╰───────────────────────────────────────────╯ │
└────────────────────────────────────────────────────────────┘
```

- **One ground, one panel.** The title bar and the sidebar are `FRAME`; the
  pages sit on a raised panel (`BG`, hairline, `RADIUS_LG`) inset from it.
- **Our own title bar.** Top left: the sidebar button, then the profile.
  Top right: Tora's state (only while she is not idle), the bell, then
  minimise / maximise / close drawn as Windows 11 draws them. The window
  is still a real Windows window - it snaps, resizes, has the shadow and
  rounded corners, and hovering maximise offers snap layouts.
- **The sidebar opens and shuts.** Open: the wordmark and icon + name rows.
  Shut: the app icon and icon rows, each naming itself on hover. It slides
  on the site's curve; the choice is remembered (`TORA_SIDEBAR`). Below
  1100px wide it shuts by itself and opens again when there is room.
- **Icons:** Lucide outlines (`assets/icons`, ISC), 18px, one stroke
  weight. A new icon is a file there plus a line in `app_widgets.ICONS`.
- **No header bar on a page.** A page has a title and its actions; Home
  greets instead ("Hey Dylan, just hold [Ctrl + Win] and ask").
- **Cards from the shell** are solid (they open over the app, not the
  desktop): **profile** (name, email, plan and use, free month, manage
  account, sign out), **notifications** (TODO: agent tasks and approvals,
  progress, usage, achievements - the bell and its dot are ready), **free
  month** (coming soon), **help** (contact support, problem report, keys
  and diagnostics).

**The page map.** Each row holds a full page's worth; nothing is a tab
with two settings on it.

| Row | What it is | Made from |
|---|---|---|
| Home | Greeting, measured stats, one hero card, recent activity by day | Home + Journal's history |
| Agents | Background work: plans, progress, approvals | Agents |
| Journal | What you learned, walkthroughs worth keeping | Journal |
| Packs | Turn industry packs on and off | Extensions |
| (pack) | A pack's own page, e.g. Accounting, while it is on | Accounting |
| Tora | Make her yours: voice, colour, how she teaches and answers | Tutor + Voice |
| Get a free month | Referral - coming soon | new |
| Settings | Opens a dialog with its own menu: General, Privacy, Models, Account, Advanced | Settings + Setup + Models |
| Help | Support, a problem report, keys and diagnostics | new |

## 3C. A page (after Wispr Flow's pages)

Built from `ui/app_parts.py`. Home is the template; every page follows it.

**The page map, revised.** Journal stops being a page: what you asked goes
to Home, review and saved walkthroughs to **Learn**.

| Page | Wispr | Hero (family · scene) | Under it |
|---|---|---|---|
| Home | Dictation | ask · Ask - "Ask about *anything* on your screen" | side card (questions, walkthroughs, day streak; plan or review); everything asked, by day |
| Insights | Insights | show · Show me - "What you've *learned*" | streak grid, apps you ask about, walkthroughs, agent tasks, plan use; achievements later |
| Agents | Notetaker | do · Do it - "Tora works while *you* don't" | "give her a first job" card, running, approvals, past tasks |
| Learn | Dictionary / Snippets | teach · Teach me - "Keep what she *taught* you" | review, saved walkthroughs, recorded lessons, captured workflows |
| Packs | Transforms | build · Build it - "Tora, *for your work*" | a grid of pack cards; "more packs coming" |
| Tora | Style | ribbon - "Make her *yours*" | voice carousel, colour, teaching style, how she answers, instructions |

Sidebar extra (later): a **Get started** checklist - first question, a
walkthrough, her voice, a pack, an agent's first job - each ticked from
real data, gone when all are.

**Rules.**

- **One level of container.** Nothing in a card is a card. Containers are
  filled (`SURFACE` on the panel), never filled *and* outlined.
- **A centred column**, at most `T.COLUMN_W` (1040px) wide. The title is in
  it and scrolls with the page.
- **A hero per page**: the page's gradient family from heytora.com drifting
  slowly, a light veil on the words' side, the headline in Plus Jakarta
  bold with one *italic* word in the family's deep colour, one line, one
  paper pill button, and the website's tour scene for that capability
  playing in a pane that rises out of the card's bottom edge (hidden when
  the card is too narrow). Drawn content: light in both themes.
- **Lists are one container** with hairline rows; the row under the
  pointer tints and shows its actions (copy...); a click opens it.
  Section labels ("TODAY") sit outside, above.
- **Quiet when fine.** No "Connected" pill, no "Nothing running": a banner
  only when she can't answer, a "Happening now" list only while something is.
- **Empty states are written**: what will be here and the one thing to do.

## 3A. Where the values live

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

`BLUE_TINT` / `BLUE_TINT_DARK` fill a chosen chip or card; `ON_COLOUR`
(white) is type and glyphs on a blue, a pack tile or a deep gradient.

**Frame vs paper.** The *frame* (window, sidebar, page ground, the app's
own cards and controls) follows light/dark through `app_theme` (`T.BG`,
`T.SURFACE`, `T.TEXT`...). *Paper* is content the app draws - the equation
card, the peeker's card, pack and capability cards on a gradient, the
windows in the tour scenes - and stays light in both themes (`PAPER.card`,
`.ink`, `.dim`, `.faint`, `.line`, `.edge`...). `STAGE` is the one dark
moment in the light flow ("Nice job.").

**Depicted apps keep their own colours.** A picture of someone else's
app - the tour's music player, spreadsheet, PDF, album covers, the
florist's site - wears that app's colours, and they live in one map,
`tour_scenes.DEPICTED`, and nowhere else (heytora-web BRAND.md §2).

**Scales.**

| | Values | Use |
|---|---|---|
| Radius | `RADIUS_SM` 8 · `MD` 12 · `LG` 16 · `XL` 20 · `pill(h)` | inputs and cells · cards, chips, wells · floating cards and pictured windows · the one card a screen is about · buttons |
| Button height | `BUTTON_H` sm 30 · md 34 · lg 44 | answers inside a card · the app · a screen's one action |
| Shadow | `SHADOW` card · float · hero | a drawn card on colour · tests, popups, pickers · sign-in, You're set, Personalize |

A pill's radius is `pill(h)` = one under half the height: Qt drops a
radius above half and the button goes square.

The families (`CAPABILITY`, `STEP`, `PACK`, `SCENE_LOOKS`, `VOICE_COLOURS`)
and the ribbon (`RIBBON`, `RIBBON_POOLS`, `RIBBON_FILLED`) are in tokens.py
too, copied from heytora-web.

## 4. Colour

### Frame
Neutral only: `T.BG`, `T.SURFACE`, `T.BORDER`, `T.TEXT*`. The single accent
is the **cursor blue `#3380FF`**. It is used for:

- primary buttons
- switches that are on
- the selected nav item
- focus rings
- links
- progress
- the "active" dot

Nothing else in the frame carries a hue.

Status colours (`OK`, `WARN`, `DANGER`) are for status only: a failed task,
a used-up plan. They are never used for decoration.

### Content: the site's gradient families
Copied from heytora-web, and moved into `ui/tokens.py` as named values when
first used. Same names, same hexes, so the app and site stay in step.

**Capabilities** (`home/what-gradient.tsx`): one family each, used wherever
that capability is the subject (Home, walkthrough cards, Tutor):

| Capability | Wash | Blobs |
|---|---|---|
| Ask | `#D7EBFF` | `#7CCBFF` `#00B6FD` `#1E7FE8` `#8FE3F0` `#E3B0FF` |
| Show me | `#D5DDFF` | `#2B63D6` `#6F7CF5` `#4F86E8` `#A48CFF` `#CFE9FF` |
| Teach me | `#F1E0FA` | `#B89CFF` `#F0A6FF` `#E48AD8` `#FFC6DE` `#D9D2FF` |
| Do it | `#D6F4F0` | `#7FB6FF` `#39D5C6` `#2FB8C8` `#8FE8D6` `#BDF2E6` |
| Build it | `#FBF3DD` | `#9CE9DA` `#FFE08A` `#F6B96B` `#FFF0C4` `#B6EE7A` |

**Packs** (`home/packs.tsx`): a two-stop 145° gradient icon tile with a
white glyph. Used on Extensions, the pack's own page header, and any agent
card working for that pack:

| Pack icon | From → to |
|---|---|
| ledger (Accounting) | `#00B6FD` → `#3D7BFF` |
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
1. **A thing keeps its colour.** Accounting is cyan→blue on Extensions, on
   its page, and on its agents' cards. Never re-pick per screen.
2. **Gradients fill a whole surface:** a hero card, an icon tile, an
   onboarding visual. Never a gradient border, gradient text, or a tinted
   row.
3. **Type on a gradient** is near-black (`#292929`) on the pale families and
   white only on the deep ones (pack tiles). Check contrast. White on the
   site's `#00B6FD` fails.
4. **At most one large gradient per screen.** Small pack tiles can repeat.
   The Home hero is the large one on Home. A second large one turns colour
   into wallpaper.
5. Gradients look the same in light and dark: they are content, not frame.

## 5. Type

Plus Jakarta Sans everywhere (bundled, `assets/fonts`, SIL OFL), unhinted
with +0.3px tracking at body sizes (`tokens.app_font()`). Bold gets bolder
as type grows. Labels are the only uppercase, the only positive tracking.

| Tier | Size | Weight | Tracking |
|---|---|---|---|
| Page title | 28 | 700 | -0.4px |
| Card title | 15 | 600 | -0.15px |
| Body | 13 | 400 | +0.3px |
| Secondary | 12 | 400 | +0.3px |
| Label (uppercase) | 10 | 600 | +1.5px |
| Metric | 28 | 700 | -0.4px |
| Onboarding title | 30 | 700 | -0.5px |

Sentence case with full stops in copy ("Never leave the screen."), as on the
site. No exclamation marks. No emoji in UI text.

## 6. Components and motion

- **Buttons** are full pills. One primary (blue) per view; the rest are
  outline pills. Destructive = red outline, never red fill.
- **Switches** slide on the site's curve. **Cards**: 12px radius, hairline,
  no shadow. Shadows are for floating things only.
- **Keycaps** (the site's scarce mark): a plate with a 2px side hairline.
  One per screen at most: the hotkey in onboarding, the hotkey on Home. A
  shortcut mentioned elsewhere is set as an uppercase label, not a cap.
- **Motion** uses `tokens.ease()` = the site's `cubic-bezier(0.22, 1, 0.36,
  1)`: 160ms for small state changes, 260ms for panels and steps.
  Things fade and settle; they don't bounce or slide across the screen.
  Respect Windows' "animation effects" off.

## 7. Onboarding (after Wispr Flow)

The model is Wispr Flow's Windows onboarding (walked through 2 Oct 2026,
15 screens). We take its **structure and its demos**. Its visual details
stay out: the serif display face, the purple, the black buttons, the
hand-drawn illustrations. Ours are §4–§6.

### 7.1 What Wispr Flow does, and what we take

| # | Their pattern | Take it? |
|---|---|---|
| 1 | **A phase bar across the top**: SIGN UP › PERMISSIONS › SET UP › LEARN › PERSONALIZE in tracked caps, with a thin accent line under it that grows with progress. You always know where you are and how much is left, without "step 7 of 14". | Yes. Our label tier (§5), the accent line in cursor blue. |
| 2 | **Two layouts, chosen by what the step is.** *Questions* use a split: a left column (title, one line, choices, full-width Continue pinned to the bottom) beside a right-hand picture panel. *Tests* use a centred layout: title, one line, then a single floating white card holding the live test. | Yes. Exactly these two layouts, nothing else. |
| 3 | **The picture panel reacts to the answer.** Pick "Dictation" and its polaroid comes forward in colour while "Notetaker" stays grey; pick both and both light up. | Yes. Our panel is the step's gradient (§4) with the chosen capability's or pack's card coming forward. |
| 4 | **Choices are chips or option cards**, and Continue stays greyed out until something is chosen. A chosen chip or card fills with a pale accent. | Yes. Pale fill = `ACCENT_SOFT`. |
| 5 | **Every test is a real action with a yes/no question about what you saw.** "Do you see purple bars while you speak?" [Change microphone] [Yes]. "Does the button turn purple while pressing it?" with a keycap that lights while held, an alternative shortcut and "Edit shortcut". | Yes. This is the heart of it. Our questions: "Do the bars move when you talk?", "Does the key light up while you hold it?" |
| 6 | **The Learn step is the product working inside a pretend app.** A fake Slack thread ("Hey Dylan, what's up?"); you hold the key, speak, and your words appear in the reply box. Skip sits top-right. | Yes. Ours: a pretend screen where you ask Tora something and she points at the answer with the real cursor (§7.2, step 7). |
| 7 | **The payoff screen flips to dark** for the celebration: "Nice job!", your own measured speed against the average, a playful drawing. | Yes to the flip and to measuring something real. No to inventing numbers: we show only what we measured in their session (§8: no invented stats). |
| 8 | A savings calculator ("29 hours a week!") driven by a slider. | No. It's a projected claim, and the site's rule is no stat we can't stand behind. |
| 9 | **The background on test screens is the brand mark**, huge and at very low contrast (their logo's loops). | Yes. Ours is the logo's swoosh, at `BORDER` contrast. |
| 10 | Privacy as two option cards ("Improve the model" / "Don't use my data"), with "change this later in Settings". | Yes, as our trust step: what Tora sees and when. |
| 11 | Survey questions: where you heard about us, what you do for work, where you type. | Partly. "What do you do?" earns its place because it picks the packs we switch on (Accounting…). Where you heard about us: one optional screen at most. |
| 12 | Ends on a referral card ("Give a month of Pro"). | Later, once the site's referral system exists (heytora-web REFERRAL-PLAN.md). |
| 13 | Constant furniture: Back top-left, a Help pill bottom-left, a language picker top-right. | Back and Help, yes. Language: no, Tora is English-only today. |
| 14 | Before the app opens, the download page tells you what to do in three steps, with an animation of the installer. | Yes, but it's the website's job (heytora-web), not the app's. |

### 7.2 Our flow (built: `ui/setup_wizard.py`, `ui/onboarding_parts.py`)

The steps prove what Tora is, rather than copying another app's list: she
hears you, the key works, she sees and points, she explains and draws.
Q = split question layout, T = centred test layout.

| Phase | Page id | Screen | Layout | What proves it |
|---|---|---|---|---|
| Sign up | `connect` | Sign in to HeyTora: a centred card with the logo, on the ribbon | T | Signed in. Continue is locked until then |
| | `welcome` | Hi, I'm Tora. Picture: the website's five "What it does" scenes, moving (`ui/tour_scenes.py`, a port of heytora-web `home/tour-scenes.tsx`) | Q | Get started |
| | `work` | Tell us about yourself: name, and what you do (chips). Matching pack cards slide in, in colour | Q | A chip |
| Permissions | `privacy` | We look when you ask. Not before. The one real control: **Skip private windows** (the `privacy_guard` switch). Picture: the website's peeker, asleep on the claim, waking while the pointer is on the card | Q | A choice |
| | `mic` | Test your microphone: "Do the bars move when you talk?" | T | [Change microphone] [Yes] |
| Set up | `ready` | Getting Tora ready (only when opened by the sign-in gate) | T | The app has started |
| | `shortcut` | Test your shortcut: "Does the key light up while you hold it?" | T | The key lit at least once |
| Learn | `try` | Ask Tora about this: a worked sum with one wrong line. Said in full: "Hey Tora, what's wrong with this equation?" | T | Tora pointed (timed). Skip |
| | `payoff` | Nice job (dark): only what was measured. Left out if nothing was | T | None, a moment |
| Personalize | `voice` | Pick her voice: one voice at a time, big, its waveform in its own colour moving with the real clip; arrows or ←/→. Nothing else on the page | T | A choice |
| | `personal` | Make her yours: her colour (six swatches, a live pointer), start with Windows, always listening, floating panel, how she answers (Live / Classic) | T | Choices |
| | `done` | You're set: one card - an encouraging word (be curious; anything on screen is fair game), the hotkey keycap, Open HeyTora - on the ribbon filled with the site's colours | T | Opens the app on Home |

**The words hide while the shortcut is held.** On a Learn screen the
heading and "Hold Ctrl + Win and ask Tora: ..." fade out the moment Tora
starts listening, and come back if nothing came of it. Tora reads the
whole screen, and with the instruction showing she read it out instead
of looking at the sum.

**Not in the onboarding, on purpose.** "What should Tora help with?"
(she does all of it anyway) and the approvals choice ("ask first" stays
the default, in Settings; the moment to ask is the first time an agent
needs it). A "use my data to improve Tora" choice waits until there is
such a thing to switch.

**Two halves.** The sign-in gate shows this window before the overlay,
hotkey and voice exist. Up to `mic` it needs nothing else. Leaving `mic`
emits `ready`, the gate lets the app start, and `main.py` calls
`attach(live)` on the same window, which moves on to `shortcut`.
Opened from a running app (the tray's "Run setup again"), `attach` has
already happened and `ready` is skipped. After a sign-out, the gate opens
it at `connect` only, and signing in finishes it.

**The Learn asks are real asks.** They count against the user's plan like
any other question, so the first launch is no way around the limits.

**Voices and colour.** The 12 hellos are recorded once by whoever
releases (`assets/voices/make_voices.py`) and ship with the app: choosing
a voice never spends a talk message. Tora's colour (`TORA_CURSOR_COLOUR`)
changes only her pointer; the app's accent stays the cursor blue.

**The shortcut test borrows the hotkey.** While `shortcut` is up,
`main.py` routes the hotkey to the window (`claim_hotkey`), so holding it
lights the key without starting a conversation. The Learn screens do not
borrow it: there the real Tora answers, from a real screenshot of the
pretend app, and the real cursor points. That uses one or two of the
user's talk messages.

Rules that carry over from Wispr Flow:

- Continue is disabled until the step is answered or proven.
- Back on every screen after the first.
- Skip only on Learn screens.
- Any test can be redone from Settings.
- Every Learn demo uses the **real** overlay and voice, not a recording.

### 7.4 One Learn step

Two second demos were tried and dropped. A triangle ("teach me how to
find the missing side") sent Tora into planning an app walkthrough
against a screen with no controls, and she stalled. A settings card of
real switches worked but added length without proving anything new. The
equation alone proves she sees, points and explains.

**Drawing after a screen look.** On the equation she boxed the wrong
line correctly (look_at_screen finds it in the image) and then drew a
red circle around nothing: a `draw` call with coordinates the voice model
made up, since look_at_screen answers in words. For 20 s after
look_at_screen highlights something, coordinate drawings are skipped;
anchored ones (`[CIRCLE:@Save]`) still draw (companion_manager
`_tool_draw`). A drawing counts as an answer on the Learn screen, as a
point does.

### 7.3 Decided

- **Picture panel:** the step's gradient (§4) with something that answers
  the choices: the carousel, pack cards, capability cards, the peeker, the
  desktop. No illustrations for now. The carousel's images are PNGs in
  `assets/onboarding/` (drawn by `assets/onboarding/make_slides.py`); a
  real screenshot saved under the same name replaces one.
- **Motion:** every choice animates on the site's curve (chips and cards
  fill, ticks draw, pack cards slide in, capability cards lift). The test
  screens' ground is HeyTora's ribbon in the site's cyan, blue and violet.
- **No "where did you hear about us?"** screen.
- **Payoff font:** Plus Jakarta Sans throughout; the moment comes from
  scale and the dark flip, not a serif.

## 7A. The floating pill (after Wispr Flow)

Seen in Wispr Flow (2 Oct 2026), for stage 5. Not built yet.

- **Small at rest.** A black pill, about 46 × 16 px, with a row of dots,
  at the bottom centre of the screen. It grows into the waveform while
  listening.
- **Movable, with snap targets.** While dragged, grey ghost pills show
  where it can land: left edge, right edge and bottom centre. Easy to
  understand at a glance. It never floats anywhere else.
- **A result card when something is said.** A dark glass card above the
  pill: logo top-left, a short hint top-right, the text, a Copy button,
  a close ✕. For Tora this is the answer text (and what she did). It
  replaces the always-on captions, which become optional (Settings).
- **A pop sound** when the pill opens. Short, soft, satisfying. One
  sound for open; nothing for every state change.

## 8. What we don't do

- Glass inside the app window. Colour in the frame. Gradient text or borders.
- A description line under page titles.
- More than one large gradient per screen.
- Emoji, exclamation marks, "unlock", "supercharge", "seamless".
- macOS window chrome or traffic-light dots (heytora-web BRAND.md §1).
- A new colour, font or radius that isn't in `ui/tokens.py`. If one is
  needed, add it there and here, together.

## 9. Order of work

| Stage | Scope | Status |
|---|---|---|
| 1 | Tokens, fonts, light/dark, main window restyle, tray click + popup | Done |
| 4 | Onboarding rebuilt to §7 | Built; needs a live run on a clean profile |
| 2 | Frame: title-only headers, window title, Home hero gradient card | Folded into A1 / A4 |
| 3 | Content colour: pack tiles on Extensions/Accounting/Agents, capability colours | |
| 4A | Consolidation: every value into `ui/tokens.py`, one `ui/kit.py`, a test that keeps it (§3A) | Done |
| A1 | App shell (§3B): own title bar, sidebar that opens and shuts, page map, no description lines, Home greeting, profile / notifications / free month / help cards | Done |
| A1b | Page template (§3C): hero, side card, history list, centred column, no outlined cards; Home rebuilt | Done |
| A2 | Settings as a dialog with its own menu (General, Privacy, Models, Account, Advanced); Setup and Models leave the sidebar | Next |
| A3 | Notifications feed: agent tasks, approvals, progress, usage, achievements | |
| A4 | Insights, then Agents, Learn (Journal leaves the sidebar), Packs, Tora - each on the §3C template | |
| A5 | Home extras: replay a walkthrough from history (needs a manager entry point), Get started checklist | |
| 5 | Glass and the pill: §7A pill with snap targets, result card, pop sound, captions optional; tray popup to glass; panel, captions and buddy onto shared tokens; `agents/ui/orbs.py` (worker 1) | |

Each stage: a mock-up screenshot first, then code, then this table updated.
