# Any app, nothing configured

**The question:** how does Tora act on *anything* on this PC, without
someone first writing an adapter for each app?

**Status:** design document. Nothing here is built. Measurements are from
this machine on 23 September 2026 and are reproducible with the probes
described in §9.

**Written for:** whoever builds it — the user deciding what is worth the
effort, and the session that implements it.

---

## 1. The honest answer first

There is no universal "do this in that app" API on Windows. Anyone who
tells you otherwise is selling screenshots and clicks.

What there *is* — and it is a lot — is **six independent mechanisms, each
of which covers a different slice of every app on the machine, none of
which needs the app's cooperation, and none of which needs a line of code
per app.** Today Tora uses four of them, but only through eight
hand-written adapters, so the coverage they could give is locked behind
files nobody has written yet.

The shift this document proposes is not "add more apps". It is:

> **Stop writing adapters. Write *capability providers* — things that can
> look at any app and say what it can be asked — and let the app list build
> itself.**

An adapter then becomes an optimisation for the handful of apps worth
hand-tuning (Spotify, VS Code), not the price of entry for the other 215.

---

## 2. What this machine already exposes, measured

| Source | Count here | Needs an adapter? |
|---|---|---|
| Launchable apps (`Get-StartApps`) | **223** | no |
| Start Menu shortcuts | 169 | no |
| Registered URI schemes (`HKCR` → `URL Protocol`) | **298** | no |
| `App Paths` executables | 82 | no |
| COM automation objects (Excel, Word, Outlook, PowerPoint, Access, Shell…) | 8+ | no |
| Windows 11 **App Actions** registry | **0** | — not populated on this build |

Tora currently offers **9 apps and 55 actions**. The machine is offering
two orders of magnitude more surface than that, and almost all of it is
already enumerable.

### UI Automation, measured across every open window

Depth 8, 8-second budget per window:

| Window | Framework | Nodes | Addressable | Menu items | Time |
|---|---|---:|---:|---:|---:|
| Spotify (web) | Chromium | 268 | **135** | 0 | 0.4 s |
| File Explorer | Win32 shell | 203 | **127** | 1 | 0.7 s |
| Brave | Chromium | 288 | 98 | 0 | 0.5 s |
| Edge | Chromium | 206 | 73 | 0 | 0.4 s |
| WhatsApp | WinUI 3 | 82 | 17 | 1 | 0.2 s |
| Gmail tab | Chromium | 26 | **3** | 0 | 0.0 s |
| Tora's own overlay | — | 0 | 0 | 0 | — |

Four things fall out of that table, and they shape everything below.

**a) Enumeration is affordable.** Two to three hundred nodes in under a
second. That is fine per *request*. It is far too slow to do on every
conversational turn, so it must be lazy and cached — see §6.

**b) Coverage is wildly uneven, and the app's framework predicts it.**
Win32 and WinUI expose their chrome faithfully. Chromium exposes its
*browser* chrome (135 addressable controls in the Spotify window — all
toolbar, tabs and window buttons) and almost nothing of the *page*.

**c) The menu bar is dead.** One menu item across ten windows. The
classic generic path — "drive File → Export in any app" — does not exist
on a modern desktop. Any design that leans on it is designing for 2005.

**d) Chromium will not open up just because you ask twice.** Received
wisdom is that Chromium builds its accessibility tree on demand when a
client queries it. On this build it does not: the Gmail window reported
26 nodes and 3 addressable controls on three consecutive passes, 1.5 s
apart. **For web content, UI Automation is not the answer** — the
browser's own automation surface is (§4.6).

---

## 3. The principle: capability providers, not adapters

Today:

```
AppControl → [Spotify adapter] [VS Code adapter] [Browsers adapter] …
                    ↓                  ↓                ↓
              hand-written       hand-written     hand-written
```

Every app is a file someone wrote. An app nobody wrote is invisible, and
"invisible" is indistinguishable from "Tora can't do that" — which is
exactly how a Spotify request became an Edge tab last week.

Proposed:

```
AppControl
  ├─ providers (generic, app-agnostic, zero config)
  │    launcher · deep-link · shell-verb · CLI · UIA · COM · browser · vision
  │                              ↓
  │                    asked about ANY app, answer what it can do
  │
  └─ adapters (optional, hand-tuned, for apps worth the effort)
       spotify · vscode · windows
```

An `AppProfile` is then **discovered, not declared**: a name, the ways to
reach it, and the actions each provider can synthesise for it right now.
The adapter, where one exists, simply wins the ranking.

---

## 4. The six mechanisms

Ranked by the existing `Tier` ladder — quietest and most reliable first.

### 4.1 Launch and deep link · Tier DEEP_LINK · covers *every* app

298 URI schemes and 223 launchable apps, already enumerable, already
half-used by `discovery.py`. This alone answers "open X", "open X with
this file", and for the ~300 apps with a scheme, a surprising amount of
"open X **at** Y" — `capcut:`, `claude:`, `bingmaps:`, `antigravity:`.

*Gap:* nothing knows what a scheme's *arguments* mean. `spotify:` is
documented; `camtasiaauth:` is not. Schemes should be offered as
"open this URI" and no more, unless an adapter says otherwise.

*Cost:* free, instant, no window moves.

### 4.2 UI Automation · Tier UI_BACKGROUND · covers native apps

Already in `agents/control/uia.py`, used as a fallback. It should be a
**first-class action source**: for any window, every named control with an
`Invoke`, `Toggle`, `Value`, `ExpandCollapse` or `SelectionItem` pattern
*is* an action, discoverable in half a second.

That is 127 real actions in File Explorer and 17 in WhatsApp that nobody
has to write down. "Switch File Explorer to Details view" already works —
there is a `RadioButton "Details"` with `Select`.

*Gaps:* Chromium page content (§4.6); a shallow initial tree in some WinUI
apps; names that are icons ("Workspace Button").

*Cost:* 0.2–0.7 s per window, one COM round trip per node. Cacheable.

### 4.3 Shell verbs and file associations · Tier SYSTEM · covers every file

Every file type carries verbs — `open`, `edit`, `print`, `printto`,
`runas` — and every app that registered itself as a handler can be asked
to take a file. This is how "print this", "open this in Photoshop",
"edit that with Notepad" become generic rather than per-app.

*Cost:* free. *Risk:* `print` and `runas` are CHANGES and SENSITIVE.

### 4.4 COM automation · Tier API · covers Office and the shell, deeply

Excel, Word, Outlook, PowerPoint, Access and `Shell.Application` are all
registered here. COM automation is not a fallback — it is a *better* API
than any adapter would hand-write: read a cell, send a mail, add a slide,
enumerate Explorer windows, all without a pixel moving.

*Blocker:* `pywin32` is **not installed**, and CLAUDE.md forbids new
dependencies without asking. This one is worth asking about: it converts
the entire Office suite from "drive the UI and hope" into a real API.

### 4.5 Command-line interfaces · Tier API · covers developer tools

`discovery.run_cli` already exists. Generalise it: an app with a CLI on
`PATH` or in `App Paths` can be offered "run this command", gated behind
confirmation, with the app's own `--help` read once and cached as the
description.

### 4.6 Browser automation · Tier API · covers the web, which UIA cannot

The measurement in §2 is decisive: Chromium gives 3 addressable controls
for a full Gmail window. Everything a person actually does in a browser is
invisible to UI Automation.

Options, best first:

1. **DevTools Protocol** over `--remote-debugging-port`. Full DOM, real
   clicks, real reads. Requires launching the browser with the flag, which
   means Tora owning a browser profile or asking the user once.
2. **`--force-renderer-accessibility`**, which makes the page tree appear
   in UIA. Cheaper to adopt, still requires a restart of the browser.
3. **A browser extension** speaking to Tora over a local socket. Most
   capable, most work, most trust required.

Without one of these, "any app" honestly means "any app that is not a web
page", and most of a modern desktop is web pages.

### 4.7 Vision and synthetic input · Tier VISION · the floor

Already built. It must stay the *last* rung: it is slow, it costs money
per look, it moves the user's cursor, and it is the only rung that can be
confidently wrong. Its real job is to handle what the five rungs above
genuinely cannot, and to be *rare*.

### Not viable today: Windows App Actions

The registry keys are **empty on this build**. It is the right long-term
answer — apps declaring their own actions to the OS — and it should be
re-probed every few months, but nothing can be built on it now.

---

## 5. How an app gets a name and a verb with no configuration

Naming is the part that decides whether this feels magic or broken. The
user says "turn it down in Spotify", and something must map *Spotify* to a
process, a window and a set of actions.

```
"spotify"
   ├─ running window title / process name          ← strongest, free
   ├─ Start Menu shortcut name (223 of them)
   ├─ App Paths key, executable stem
   ├─ URI scheme name
   └─ the user's own word for it ("my music")      ← learned, §7
```

Verbs come from the provider that answered. A UIA control called
"Details" with a `Select` pattern becomes `explorer.select_details`; a
button called "Bookmark this tab" becomes `brave.bookmark_this_tab`. The
action's description is the control's name and type, which is exactly what
a person would have called it.

**The model never has to know any of this in advance.** It calls
`desktop_action(app='whatsapp', action='list')` and gets back what that
app can do *right now*. That path already exists and is already tested —
what changes is that the answer stops being empty for apps nobody wrote.

---

## 6. Latency and cost, which decide the design

From the cost work (M28): a spoken turn costs about $0.0049, and the
per-turn tool payload is re-billed on **every** turn. So:

* **Never enumerate on a turn.** 223 apps × their actions in the tool
  declaration would cost more per turn than the conversation.
* **Enumerate on request.** `action='list'` for one app is 0.2–0.7 s and
  costs nothing but time.
* **Cache per window handle**, invalidated on window title change or after
  ~30 s. A second question about the same app is then instant.
* **The declaration stays a fixed size**: the *names* of the apps that are
  running, and the instruction to call `list`. Not their actions.

This is the same lesson as the screen-context line in M28: what is sent
every turn must be small, and what is large must be fetched only when
asked for.

---

## 7. It should get better at your machine as you use it

Every successful resolution is evidence worth keeping:

* "when they said *my music*, spotify was the right app" → an alias;
* "`explorer.select_details` was invoked and verified" → promote it to the
  fast path for this window class;
* "`whatsapp.new_chat` failed three times via UIA" → stop offering it, or
  drop to the next rung sooner.

Stored in `%LOCALAPPDATA%\Tora\learned_actions.json`, in plain text, next
to the standing permissions from DC-16 — for the same reason: if you
cannot read it, you cannot correct it.

This is what makes the difference between "generic" and "good". A generic
system that never learns is a system that is equally mediocre everywhere
forever.

---

## 8. Safety, which gets harder, not easier

An adapter author decides the risk of each action by hand. A synthesised
action has nobody to decide it, and "invoke any button in any app" is a
genuinely dangerous primitive — the button might say *Delete account*.

Rules this design commits to:

1. **Risk is inferred, then floor-ed.** A control's verb and name give a
   first guess (`Close`, `Delete`, `Remove`, `Send`, `Pay`, `Submit`,
   `Uninstall` → SENSITIVE), and anything synthesised that cannot be
   classified defaults to CHANGES — which always asks.
2. **A synthesised action never gets a standing grant** on first use. "Always
   allow" has to name something a person has actually seen work.
3. **Read before write.** A provider must be able to *describe* an app
   before it is allowed to *act* on it, so "what can WhatsApp do" is
   always safe.
4. **Tora's own windows stay invisible**, as they already are — an agent
   that can press its own approval dialog is a loop.
5. **Every synthesised action is verified** (DC-24) or reported unverified.
   Generic actions are exactly where silent failure is most likely.

---

## 9. Milestones

Each becomes its own spec document, then one commit, in roughly this
order. Sizes: **S** ≈ a sitting · **M** ≈ a day · **L** ≈ multi-day.

### Phase 1 — the generic app exists at all

| id | milestone | size |
|---|---|---|
| **UC-01** | `CapabilityProvider` protocol and an `AppProfile` built by discovery, not declaration. Adapters become providers that happen to be hand-written. | M |
| **UC-02** | **Launcher provider.** Every one of the 223 `Get-StartApps` entries becomes `open`, addressable by its Start Menu name, with no file written. | M |
| **UC-03** | **Name resolution** across window titles, Start Menu, App Paths and URI schemes, with a confidence score and a question when it is ambiguous. | M |
| **UC-04** | **UIA provider.** Every named control with an actionable pattern in any window becomes an action, cached per window handle. | L |
| **UC-05** | `action='list'` answers for an app with no adapter — the moment the app list stops being nine things. | S |
| **UC-06** | **Risk inference** for synthesised actions, with the SENSITIVE word list and a CHANGES floor. Tested against every control on this machine. | M |

### Phase 2 — the mechanisms that need a decision

| id | milestone | size |
|---|---|---|
| **UC-07** | **Shell-verb provider**: open / edit / print / open-with, for any file and any registered handler. | M |
| **UC-08** | **COM provider** for Office and the shell. *Needs a decision on `pywin32`.* | L |
| **UC-09** | **CLI provider**: detect a CLI, read `--help` once, cache it as the action's description. | M |
| **UC-10** | **Browser automation.** Pick one of the three routes in §4.6 and commit. Without this, "any app" excludes the web. | L |

### Phase 3 — making it good rather than broad

| id | milestone | size |
|---|---|---|
| **UC-11** | Per-window action cache with title-change invalidation and a 30 s floor. | M |
| **UC-12** | Learned aliases — "my music", "the big monitor", "my project". | M |
| **UC-13** | Learned fast paths: a verified synthesised action gets promoted. | M |
| **UC-14** | Failure memory: stop offering what has repeatedly failed on this machine. | M |
| **UC-15** | **Disambiguation dialogue** — one short spoken question when two apps or two controls match, instead of guessing. | M |
| **UC-16** | Window-class heuristics: know that Chromium means "ask the browser", WinUI means "go deeper", Win32 means "the tree is honest". | M |

### Phase 4 — the long tail

| id | milestone | size |
|---|---|---|
| **UC-17** | Re-probe **Windows App Actions** and adopt it if the registry has become populated. | S |
| **UC-18** | **MCP servers as a provider** — anything the ecosystem exposes becomes a desktop action under the same permission model. | L |
| **UC-19** | A **capability report** in the app window: every app, what Tora can do with it, and by which mechanism. The honest answer to "what can you actually do?" | M |
| **UC-20** | **Recording**: the user does a thing once, Tora records the UIA path and replays it as a named action. The escape hatch for everything else. | L |

---

## 10. What will still need a hand-written adapter

Being honest about the ceiling:

* **Anything needing an account** — Spotify Web API, Google, Slack. OAuth
  is per-service by nature.
* **Anything whose meaning is not in its UI** — "reconcile the bank
  statement" is domain knowledge, not a button.
* **Apps whose controls are unnamed icons.** Generic naming fails when the
  app's own accessibility is poor; vision is the fallback and it is right
  that it is expensive.
* **Anything where being wrong is expensive.** Payments and deletions
  should stay hand-written, deliberate and SENSITIVE.

A good outcome is not "no adapters". It is **eight adapters for the things
worth tuning, and everything else working anyway.**

---

## 11. Reproducing the measurements

Three probes, kept out of the repo, described so they can be rewritten:

1. **Surface probe** — walk `HKCR` for `URL Protocol`, `App Paths`, the two
   Start Menu trees, the Office ProgIDs and the App Actions registry keys;
   count `(Get-StartApps).Count`.
2. **Window probe** — for every top-level window, walk the UIA tree to
   depth 8 with a node cap and a time budget, counting nodes and those
   carrying an actionable pattern.
3. **Chromium probe** — walk one browser window three times, 1.5 s apart,
   and compare node counts, to see whether the page tree ever appears.

Re-run all three before building UC-04 or UC-10: the answers change with
Windows and Chromium versions, and this document's conclusions are only as
current as its numbers.
