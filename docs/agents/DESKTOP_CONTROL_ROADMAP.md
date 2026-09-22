# Desktop control: the road from "it sometimes works" to "it always works"

**Status:** wave 1 is built — DC-01, 02, 05, 06, 07, 08, 09 and 10, in
`milestones/DC-01-10-wave-1-arguments.md`. Everything else is still a plan.
Each remaining milestone becomes its own spec document and then one
commit-sized piece of work, in roughly the order given.

**Written for:** whoever builds the next piece — the user reading it to
choose what matters, and the Claude Code session that will implement it.

---

## 0. Why this document exists

Two field reports, 23 September 2026:

> "please play this song on Spotify or play this playlist — it's not being
> able to do this anymore. It did work initially, but now it just gets
> stuck. I see it pop up with the approve message. But I click approve, it
> doesn't go through with it."

> "I say, please reduce the audio. It went and tried doing it, but the
> audio actually **increased**."

Those are not two bugs. They are one bug wearing two hats, plus a logging
system that hid it. This document starts from that diagnosis, walks the
whole pipeline from microphone to outcome, and turns every weak joint into
a numbered milestone.

---

## 1. What the evidence actually says

> **Note, after wave 1:** §1.1, §1.2 and §1.3 are fixed — they were the
> reported bug. They are kept here word for word because they are the
> reasoning the rest of the plan rests on, and because the next person to
> find a silent failure should recognise the shape of it. §1.4 and §1.5 are
> still true.

### 1.1 The log is lying to us

`ai/gemini_realtime.py::_run_tool` logs:

```python
_log.info("tool %s -> %s", call.name,
          "error: " + str(result["error"])[:120] if result.get("error") else "ok")
```

It reports `ok` unless the result dict has an `"error"` key. But
`_tool_desktop_action` never returns one. A **denied** action returns
`{"ok": False, "note": "The user did not approve it"}`. A **failed** action
returns `{"ok": False, ..., "result": "spotify.play_playlist needs playlist"}`.
Both have no `"error"` key. Both log:

```
tora.realtime: tool desktop_action -> ok
```

Every single failure in the user's session logged as success. This is why
the problem looks mysterious: **there is no record of anything going
wrong.** The control layer itself (`agents/control/*`) logs nothing at all
— not the app, not the action, not the tier used, not the arguments, not
the outcome.

Evidence from `tora.log`, 00:16:52 → 00:17:06:

```
00:16:52  tool call: desktop_action
00:17:06  tool desktop_action -> ok        ← fourteen seconds, reported as fine
00:17:14  turn: realtime                   ← user asks again
00:17:27  turn: realtime                   ← and again; no tool call at all
```

Fourteen seconds is the approval dialog sitting open. During all of it the
live model is **blocked on the tool result and cannot say a word**, which
is the "it gets stuck" the user described.

### 1.2 Arguments are silently thrown away, in two different places

**First place** — `companion_manager.py::_tool_desktop_action`:

```python
try:
    parsed = _json.loads(raw)
    arguments = parsed if isinstance(parsed, dict) else {}
except ValueError:
    arguments = {}          # ← the model's arguments, gone, silently
```

The live model passes arguments as a JSON *string* (`arguments_json`).
When it emits `{'direction': 'down'}` with single quotes, or a bare
`down`, or wraps it in prose, `json.loads` raises and the arguments become
`{}`. Nothing is logged. The action then runs **with its defaults**.

**Second place** — `agents/control/registry.py::run`:

```python
unknown = [k for k in args if k not in {p.name for p in found.params}]
for k in unknown:
    args.pop(k)             # ← a misnamed argument, gone, silently
```

If the model says `{"level": "down"}` or `{"amount": -20}` instead of
`{"direction": "down"}`, the argument is dropped. `unknown` is stashed in
`Outcome.data` and never surfaced to anyone. The action runs **with its
defaults**.

### 1.3 The default is an action

`agents/control/apps/windows_os.py` and `apps/media.py` both declare:

```python
async def volume(direction: str = "up", steps: int = 2) -> str:
```

So: "reduce the audio" → the model's `direction: "down"` is lost by 1.2 →
`volume()` runs → `direction` defaults to `"up"` → **the volume goes up.**

That is the exact reported symptom, and the mechanism is airtight. There is
no other path in the code that turns "reduce" into an increase. It also
means the Spotify failures are almost certainly the same fault: `play_playlist`
requires `playlist`, the argument is lost, and the action returns
`spotify.play_playlist needs playlist` — which logs as `ok`, so Tora reads
back something vague and the user sees nothing happen.

`Param("direction", "up | down", choices=("up", "down"))` declares its legal
values, and **nothing ever checks them**. `choices` is decoration.

### 1.4 Success is assumed, never observed

`registry.run` returns `Outcome(ok=True, ...)` whenever the coroutine did
not raise. Tapping a media key "succeeds" whether or not any application
was listening. Opening a `spotify:` URI "succeeds" whether or not Spotify
was installed, running, or logged in. Nothing reads the volume back.
Nothing asks what is playing afterwards. **Tora reports what it attempted,
not what happened.**

### 1.5 The same action name exists on two apps

`windows.volume` and `spotify.volume` both exist, both tap the same global
media keys, and nothing decides between them. "Turn it down" is ambiguous
and resolved by whatever the model guessed.

---

## 2. The pipeline, end to end

Every desktop request passes through eight stages. Each one can lose the
request, and today most of them can lose it *silently*.

```
 ┌─ 0 CAPTURE ──────────────────────────────────────────────────────────┐
 │  mic → VAD → wake word → Gemini Live audio stream                    │
 │  owns: audio/ambient_listener.py, audio/capture.py                   │
 └──────────────────────────┬───────────────────────────────────────────┘
                            ▼
 ┌─ 1 UNDERSTAND ───────────────────────────────────────────────────────┐
 │  live model decides: is this a desktop action? which app? which      │
 │  action? what arguments?                                              │
 │  owns: ai/gemini_realtime.py (tool declarations), the system prompt   │
 │  ✗ no schema for arguments — a free-text JSON blob                    │
 └──────────────────────────┬───────────────────────────────────────────┘
                            ▼
 ┌─ 2 RESOLVE ──────────────────────────────────────────────────────────┐
 │  app name → AppControl; action name → Action; arguments → params      │
 │  owns: agents/control/registry.py, control_tools.py                   │
 │  ✗ parse failure = {} · ✗ unknown names dropped · ✗ choices unchecked │
 └──────────────────────────┬───────────────────────────────────────────┘
                            ▼
 ┌─ 3 PERMIT ───────────────────────────────────────────────────────────┐
 │  Risk → confirm? → dialog → approve / deny / always-allow             │
 │  owns: agent_manager.request_confirmation, agents/ui/approval.py      │
 │  ✗ blocks the model for the whole dialog · ✗ timeout looks like "no"  │
 └──────────────────────────┬───────────────────────────────────────────┘
                            ▼
 ┌─ 4 EXECUTE ──────────────────────────────────────────────────────────┐
 │  the tier ladder: APP_ACTION → API → DEEP_LINK → SYSTEM →             │
 │  UI_BACKGROUND → UI_FOREGROUND → VISION                               │
 │  owns: agents/control/apps/*, system.py, uia.py, discovery.py         │
 │  ✗ one tier per action; no fallback when the chosen tier no-ops       │
 └──────────────────────────┬───────────────────────────────────────────┘
                            ▼
 ┌─ 5 VERIFY ───────────────────────────────────────────────────────────┐
 │  did the world actually change?                                       │
 │  owns: nothing. This stage does not exist.                            │
 └──────────────────────────┬───────────────────────────────────────────┘
                            ▼
 ┌─ 6 REPORT ───────────────────────────────────────────────────────────┐
 │  Outcome → dict → the model → spoken answer                           │
 │  ✗ "ok" means "did not raise" · ✗ failure text is not written to be   │
 │    spoken aloud                                                       │
 └──────────────────────────┬───────────────────────────────────────────┘
                            ▼
 ┌─ 7 REMEMBER ─────────────────────────────────────────────────────────┐
 │  grants, aliases, "my focus playlist", what worked last time          │
 │  owns: _desktop_grants (a set, in memory, lost on restart)            │
 └──────────────────────────────────────────────────────────────────────┘
```

Cutting across all eight: **observability** (stage 7 of the old log), 
**latency**, **cost**, and **test coverage that uses the real registry**.

---

## 3. Milestones

Format for each: the symptom it removes, what "done" looks like, what it
touches, what it depends on, a size, and the proof that it worked.

Sizes: **S** ≈ one sitting · **M** ≈ a day · **L** ≈ multi-day.

---

### Phase A — Stop lying (do these first; everything else is guesswork without them)

#### DC-01 · The log tells the truth about every tool call
**Symptom** Every failure in the field log reads `-> ok`.
**Done** `_run_tool` logs the *outcome*, not the absence of an `error` key:
name, `ok`, the action key, the tier used, elapsed ms, and the first 120
chars of the message. A denial logs `denied`. A timeout logs `timeout`.
**Touches** `ai/gemini_realtime.py`
**Depends on** —
**Size** S
**Proof** A test drives a handler returning `{"ok": False}` and asserts the
log line does not contain `ok`.

#### DC-02 · The control layer keeps a decision log
**Symptom** `agents/control/*` logs nothing; there is no way to see which
app, action, tier or arguments were used.
**Done** One INFO line per attempt: `spotify.play_playlist tier=deep_link
args={playlist:…} → ok in 140ms`. One WARN per dropped/invalid argument.
Arguments are redacted through the existing logsafe helper.
**Touches** `agents/control/registry.py`, `system.py`, `uia.py`
**Depends on** —
**Size** S
**Proof** `test_control_desktop.py` asserts a log record is emitted for a
successful and a failed run.

#### DC-03 · A desktop-action journal on disk
**Symptom** "It worked initially and then stopped" cannot be investigated
after the fact.
**Done** Every attempt appended to `%LOCALAPPDATA%\Tora\control.jsonl`:
timestamp, transcript fragment, app, action, raw arguments, parsed
arguments, risk, approval outcome, tier, result, elapsed. Rotated at 5 MB.
**Touches** new `agents/control/journal.py`
**Depends on** DC-02
**Size** S
**Proof** Ten actions produce ten lines; the file survives a restart.

#### DC-04 · `tora --replay` reruns a journal line
**Symptom** Reproducing a field failure means re-enacting it by voice.
**Done** A CLI that takes a journal line id and replays that exact call
against the real registry, printing every stage's decision.
**Touches** new `tools/replay.py`
**Depends on** DC-03
**Size** M
**Proof** The volume bug is reproduced from a journal line in one command.

---

### Phase B — Never lose an argument (this is the actual reported bug)

#### DC-05 · Argument parsing never fails silently
**Symptom** `except ValueError: arguments = {}` — the model's arguments
vanish and the action runs on defaults.
**Done** A parse failure returns an error to the model naming the action
and its expected parameters, so it can retry correctly. It never runs the
action.
**Touches** `companion_manager.py::_tool_desktop_action`
**Depends on** —
**Size** S
**Proof** `arguments_json="{'direction': 'down'}"` (single quotes) returns
an error mentioning `direction`, and no media key is tapped.

#### DC-06 · A tolerant argument parser
**Symptom** The model emits single quotes, trailing commas, `None`, a bare
value, or prose around the JSON, and all of it is thrown away.
**Done** A parser that tries, in order: dict passthrough · strict JSON ·
`ast.literal_eval` · a single bare scalar bound to the action's only
required parameter · `key=value` pairs. Whatever it does, it says which
route it took in the journal.
**Touches** new `agents/control/args.py`
**Depends on** DC-05
**Size** M
**Proof** A table-driven test of 20 real malformed shapes, all resolving
correctly or erroring loudly — never silently defaulting.

#### DC-07 · An unknown argument is an error, not a shrug
**Symptom** `args.pop(k)` for anything not in `params`, then run anyway.
**Done** An unknown name returns `spotify.play_playlist has no argument
'track'; it takes: playlist` — which is a sentence the model can act on.
Near-miss names are suggested (`level` → `steps`?).
**Touches** `agents/control/registry.py::run`
**Depends on** —
**Size** S
**Proof** `run("windows", "volume", {"level": "down"})` returns `ok=False`
and no key is tapped.

#### DC-08 · `Param.choices` is enforced
**Symptom** `choices=("up","down")` is documentation, not validation.
**Done** A value outside `choices` is rejected with the legal set in the
message. Case-insensitive, and a documented alias table maps the words
people actually say (`lower`, `quieter`, `reduce`, `softer` → `down`).
**Touches** `agents/control/model.py`, `registry.py`
**Depends on** DC-07
**Size** S
**Proof** `volume(direction="quieter")` lowers the volume;
`volume(direction="sideways")` errors.

#### DC-09 · No default may choose a direction
**Symptom** `direction: str = "up"` turns a lost argument into the opposite
of what was asked.
**Done** An audit rule and a test: any parameter whose name or choices
express a direction, a target or a destructive degree must be `required`
with no default. `volume` requires `direction`.
**Touches** every `agents/control/apps/*.py`
**Depends on** DC-07
**Size** S
**Proof** A test walks the whole registry and fails on any non-required
parameter with a `choices` set.

#### DC-10 · Typed parameters, coerced once
**Symptom** `steps` arrives as `"3"`, `3.0`, or `"three"`; each action
coerces by hand, or does not.
**Done** `Param.type` is honoured centrally: integer, number, boolean,
string, path, uri. Coercion happens once in `registry.run`; actions
receive the right Python type or the call is rejected.
**Touches** `agents/control/model.py`, `registry.py`
**Depends on** DC-07
**Size** M
**Proof** `{"steps": "3"}` reaches the action as `3`; `{"steps": "lots"}`
errors.

#### DC-11 · The tool declaration carries the real schema
**Symptom** `desktop_action` takes `app`, `action` and a free-text
`arguments_json`, so the model is guessing argument names from prose.
**Done** The per-turn declaration advertises the actual parameters for the
apps present on this machine, or a compact `app.action(param, param)`
index the model can read. Balanced against the per-turn token cost
(see M28 — this payload is re-billed on every turn).
**Touches** `ai/gemini_realtime.py::tool_declarations`, `agents/control/registry.py`
**Depends on** DC-07
**Size** M
**Proof** A recorded conversation shows the model naming parameters
correctly without a retry.

#### DC-12 · A retry loop the model can actually use
**Symptom** A rejected call is a dead end; the user has to ask again.
**Done** An argument error returns a machine-readable `expected` block.
The model is instructed (and tested) to correct and re-call once, without
asking the user again.
**Touches** `companion_manager.py`, the system prompt
**Depends on** DC-05, DC-07
**Size** S
**Proof** A scripted session: bad args → error → corrected call → success,
with one spoken sentence, not two.

---

### Phase C — Approval that does not freeze the conversation

#### DC-13 · Asking does not block the model
**Symptom** The live model is blocked on the tool result for the entire
dialog — fourteen seconds of silence in the field log.
**Done** `desktop_action` returns immediately with `awaiting_approval` and
a line for Tora to say ("I need a yes for that — the box is on screen").
The action runs when the dialog resolves, and the result is delivered as a
follow-up turn.
**Touches** `companion_manager.py`, `ai/gemini_realtime.py`
**Depends on** DC-01
**Size** L
**Proof** A scripted approval taking 10 s produces speech within 1 s.

#### DC-14 · Approve by voice
**Symptom** A voice assistant that requires a mouse click for "next song"
is not hands-free.
**Done** While a confirmation is pending, "yes" / "go ahead" / "do it" /
"no" resolve it. SENSITIVE actions still require the typed word.
**Touches** `companion_manager.py`, `agent_manager.approve`
**Depends on** DC-13
**Size** M
**Proof** A pending approval is resolved by a transcript with no click.

#### DC-15 · The dialog says what will happen, in plain words
**Symptom** The reason string is `spotify.play_playlist - Open a playlist
by link, or search for it by name. (deep link, in the background)`.
**Done** One sentence a person can judge in a second: **"Play the playlist
'Focus' in Spotify."** Plus the app icon, the tier in small text, and what
"always allow" would cover.
**Touches** `agents/ui/approval.py`
**Depends on** —
**Size** M
**Proof** A snapshot test of the rendered sentence for ten actions.

#### DC-16 · Grants survive a restart
**Symptom** `_desktop_grants` is an in-memory set; every "always allow" is
forgotten when Tora restarts.
**Done** Persisted per action key, with a visible list and a way to revoke
them (the Settings page of the app window).
**Touches** `agent_manager.py`, `config.py`, `ui/app_pages.py`
**Depends on** —
**Size** S
**Proof** Allow → restart → the action runs without a dialog.

#### DC-17 · Risk tiers a person would agree with
**Symptom** Skipping a track and opening a folder both raise the same
dialog; approval fatigue teaches people to click yes without reading.
**Done** A reviewed risk table: media transport and volume are
PASSIVE/REVERSIBLE and silent by default; anything that types, navigates,
sends or deletes always asks. The default posture is stated once, in
Settings, in one sentence.
**Touches** `agents/control/apps/*`, `model.py`
**Depends on** DC-15
**Size** M
**Proof** A test asserts the risk of every registered action against a
checked-in table, so a new action cannot quietly be born SAFE.

#### DC-18 · One dialog for a burst
**Symptom** "Open VS Code and put this in the chat" raises two dialogs.
**Done** Related calls inside one turn are batched into a single dialog
listing the steps.
**Touches** `companion_manager.py`, `agents/ui/approval.py`
**Depends on** DC-13
**Size** M
**Proof** A two-action turn raises one dialog.

#### DC-19 · The dialog cannot hide behind the overlay
**Symptom** Tora's own always-on-top overlay covers the whole screen.
**Done** The approval dialog is explicitly raised above the overlay, on the
monitor the cursor is on, and is proven to be so by a geometry test.
**Touches** `agents/ui/approval.py`, `ui/overlay.py`
**Depends on** —
**Size** S
**Proof** With the overlay shown, the dialog's window is above it in Z order.

---

### Phase D — Execution that actually reaches the app

#### DC-20 · The tier ladder falls through
**Symptom** Each action is pinned to one tier. If the deep link does
nothing (Spotify closed), there is no second attempt.
**Done** An action may declare several strategies in preference order;
`run` tries each until one *verifies* (see DC-24), recording every attempt
in `Outcome.attempts`.
**Touches** `agents/control/model.py`, `registry.py`, `apps/*`
**Depends on** DC-24
**Size** L
**Proof** With Spotify closed, "next song" launches it and then skips,
reporting both steps.

#### DC-21 · Know whether the app is even there
**Symptom** Actions are offered for apps that are not installed or not
running, and fail late and confusingly.
**Done** `discovery.scan()` results are cached with a freshness stamp and
consulted before an action is offered; "Spotify isn't running — shall I
open it?" replaces a silent no-op.
**Touches** `agents/control/discovery.py`, `registry.py`
**Depends on** —
**Size** M
**Proof** With an app uninstalled, its actions are not offered and the
message names the reason.

#### DC-22 · Spotify through its Web API, not just deep links
**Symptom** Deep links cannot say "play this playlist on *this* device",
cannot read what is playing, and cannot set an exact volume.
**Done** Optional OAuth to the Spotify Web API: real search, device
targeting, transport, exact volume, now-playing readback. Deep links stay
as the no-login fallback.
**Touches** new `agents/control/apps/spotify_api.py`
**Depends on** DC-20, DC-21
**Size** L
**Proof** "Play my Focus playlist on the desktop" works with Spotify
closed and reports the track that started.

#### DC-23 · System volume by value, not by taps
**Symptom** Volume is N taps of a media key: no idea of the current level,
no "set it to 20%", and the taps go to whatever has focus.
**Done** Read and set the endpoint volume properly (Core Audio via
`IAudioEndpointVolume`). Support absolute ("set it to 20"), relative
("down a bit" = −10%), mute/unmute, and per-application volume.
**Touches** `agents/control/system.py`
**Depends on** —
**Size** M
**Proof** "Set the volume to 20 percent" leaves the endpoint at 0.20 ± 0.02,
verified by reading it back.

#### DC-24 · Every action declares how to check itself
**Symptom** Stage 5 of the pipeline does not exist. `ok=True` means "did
not raise".
**Done** An `Action` may carry a `verify` coroutine returning the observed
state. `run` calls it and downgrades `ok` when the world did not change.
Volume verifies by reading the level. Transport verifies by reading
now-playing. Opening an app verifies the process exists.
**Touches** `agents/control/model.py`, `registry.py`, `apps/*`
**Depends on** DC-23
**Size** L
**Proof** With the media key swallowed by another app, "next song" reports
failure instead of success.

#### DC-25 · The UIA tier gets the same treatment as pointing
**Symptom** `uia.act()` is the fallback for everything without an API, and
it is the least observed part of the system.
**Done** Control resolution reuses the (now fixed) pointer tiers, logs the
control it chose and why, and refuses rather than clicking an ambiguous
match.
**Touches** `agents/control/uia.py`, `ai/hybrid_pointer.py`
**Depends on** DC-02
**Size** M
**Proof** An ambiguous control name refuses with the candidates listed.

#### DC-26 · A real timeout on every tier
**Symptom** A hung COM call or a modal dialog in the target app hangs the
action, and with it the conversation.
**Done** Every tier runs under a deadline appropriate to it (media key
100 ms, deep link 2 s, UIA 5 s), and a breach is a clean failure.
**Touches** `agents/control/registry.py`
**Depends on** —
**Size** S
**Proof** A tier that sleeps forever fails in its budget and reports it.

#### DC-27 · Actions are idempotent or say they are not
**Symptom** A retry after an ambiguous failure can double-apply
("next song" twice).
**Done** Each action declares idempotency; the retry logic (DC-12) refuses
to re-run a non-idempotent action without asking.
**Touches** `agents/control/model.py`, `registry.py`
**Depends on** DC-12
**Size** S
**Proof** A failed-then-retried `next` skips one track, not two.

---

### Phase E — More of the machine under voice control

#### DC-28 · Window management worth the name
Snap left/right, move to monitor 2, minimise everything, restore, cycle,
close by name, "put this next to that". **Touches** `system.py`. **Size** M.

#### DC-29 · Files and folders by voice
Open, reveal in Explorer, copy a path, recent files, "the thing I
downloaded just now". **Touches** new `apps/files.py`. **Size** M.

#### DC-30 · Browser control beyond opening a URL
Which tabs are open, switch to one by title, close, reopen, search within
the page, read the current URL back. **Touches** `apps/browsers.py`. **Size** L.

#### DC-31 · Editors: the whole loop
VS Code and Cursor: open project, open file at a line, run a task, read the
problems panel, send a prompt to the chat and **read the reply back**.
**Touches** `apps/editors.py`. **Size** L.

#### DC-32 · Communications
Teams, Slack, Discord, Mail: status, mute, join the next meeting, "read me
the last message". Read-only first; sending is SENSITIVE from day one.
**Touches** new `apps/comms.py`. **Size** L.

#### DC-33 · Meetings
Join, leave, mute, camera, share a window, "who is in the call".
**Touches** new `apps/meetings.py`. **Size** M.

#### DC-34 · Power, display and devices
Brightness, night light, display scaling, "second monitor off", Bluetooth
device connect/disconnect, default audio device switching.
**Touches** `apps/windows_os.py`, `system.py`. **Size** M.

#### DC-35 · The app-agnostic long tail, via Windows App Actions
Windows 11 exposes registered app actions; harvest them so an app we have
never heard of is still controllable.
**Touches** new `agents/control/app_actions.py`. **Size** L.

#### DC-36 · MCP as an action source
Treat a local MCP server's tools as another tier, so anything the ecosystem
exposes becomes a desktop action with the same permission model.
**Touches** new `agents/control/mcp_source.py`. **Size** L.

---

### Phase F — Understanding what was asked

#### DC-37 · Disambiguate when two apps offer the same action
**Symptom** `windows.volume` and `spotify.volume` both exist; nothing
chooses.
**Done** A resolution order using what is focused, what is playing and what
the user said; a one-line question when it is genuinely ambiguous.
**Touches** `registry.py::find` **Size** M
**Proof** With Spotify focused, "turn it down" targets Spotify; on the
desktop, it targets the system.

#### DC-38 · "My focus playlist" resolves to a thing
Named entities the user refers to — playlists, projects, folders, people —
learned from use and stored per user. **Touches** new
`agents/control/aliases.py`. **Size** M.

#### DC-39 · Pronouns and follow-ups
"Turn it down" after "play X" refers to X. A short-lived action context
that the next request may refer to. **Touches** `companion_manager.py`. **Size** M.

#### DC-40 · Compound requests
"Open the project and put this in the chat" is two actions with an order
and a shared subject. Parse, confirm as one, run in sequence, stop on the
first failure. **Touches** `companion_manager.py`, `registry.py`. **Size** L.

#### DC-41 · Say no to the impossible, early
If nothing on this machine can do what was asked, say so in one sentence
before any dialog appears. **Touches** `registry.py::suggestions`. **Size** S.

---

### Phase G — Telling the truth out loud

#### DC-42 · Failure messages written to be spoken
**Symptom** `spotify.play_playlist needs playlist` is a developer string
read aloud to a human.
**Done** Every failure carries a spoken form: "I couldn't — you didn't say
which playlist." A test asserts no message contains a dot-separated key or
a Python type name.
**Touches** `agents/control/model.py`, every app module. **Size** M

#### DC-43 · Confirm by observation, not by intention
**Symptom** "Done!" after nothing happened.
**Done** With DC-24 in place, the spoken confirmation quotes the observed
state: "Volume's at 20 percent." / "Now playing Rubber Soul."
**Touches** `companion_manager.py`. **Depends on** DC-24. **Size** S

#### DC-44 · Never claim success on a denial
A denied action must be reported as denied, not as done. **Size** S.
**Proof** A scripted denial produces speech containing no success wording.

---

### Phase H — Proving it stays fixed

#### DC-45 · A contract test over the whole registry
Every registered action is checked for: a required-argument rule (DC-09),
enforced choices (DC-08), a spoken failure message (DC-42), a declared
risk (DC-17), a verify (DC-24), and a timeout (DC-26). A new action cannot
be added without meeting the contract. **Size** M.

#### DC-46 · A scripted end-to-end voice suite
Transcript in, mocked registry, assert the app, action and arguments that
came out. Twenty real phrasings per app, including the two field failures
verbatim. **Size** M.

#### DC-47 · A safe live smoke test
An opt-in run against the real machine that only performs reversible
actions (volume down then back, next then previous), verifies each, and
prints a table. Never run unasked. **Size** M.

#### DC-48 · Latency and cost budgets, asserted
Time from end-of-speech to the action firing, and the token cost of the
desktop path, both measured and guarded, in the spirit of
`test_cost_footprint.py`. **Size** M.

---

## 4. Suggested order

The first six are not optional and not negotiable: **DC-01, DC-02, DC-05,
DC-07, DC-09, DC-08.** Together they are perhaps a day's work and they fix
the reported bug — the volume going the wrong way and the playlist doing
nothing — while making the next failure visible instead of silent.

| Wave | Milestones | What the user notices | Status |
|---|---|---|---|
| 1 | DC-01, 02, 05, 06, 07, 08, 09, 10 | "Reduce the volume" reduces the volume. Failures are explained. | **done** — `DC-01-10-wave-1-arguments.md` |
| 2 | DC-03, 12, 42, 44 | Wrong phrasings self-correct; nothing claims success falsely. | |
| 3 | DC-13, 14, 15, 16, 19 | Approving stops freezing the conversation; "yes" works by voice. | |
| 4 | DC-23, 24, 43, 21 | Tora reports what actually happened, not what it tried. | |
| 5 | DC-17, 18, 26, 27, 45, 46 | Fewer dialogs, no double-actions, and it stays fixed. | |
| 6 | DC-20, 22, 37, 38, 39 | Spotify properly; "turn it down" knows what "it" is. | |
| 7 | DC-28…36, 40 | The rest of the machine comes under voice control. | |
| 8 | DC-04, 47, 48 | Field failures reproducible in one command. | |

---

## 5. How to use this document

1. Pick a milestone. Prefer the lowest unbuilt number in the current wave.
2. Write `docs/agents/milestones/DC-NN-<slug>.md`: the problem in the
   user's words, the current behaviour with file and line references, the
   proposed behaviour, the exact diff surface, the tests, and what is
   explicitly out of scope.
3. Build it. One milestone, one commit, prefix `[mgr]`.
4. Add a line to `MILESTONES.md` describing what changed and why, in the
   register that file already uses.
5. Update the wave table above.

A milestone that cannot state its **Proof** as a test is not ready to be
built; it is still a wish.
