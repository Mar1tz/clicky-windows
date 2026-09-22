# Wave 1 · DC-01, 02, 05, 06, 07, 08, 09, 10 — an argument is never lost

Built together, in one commit, because they are one bug with four doors.
The roadmap asks for a document per milestone; these eight share a single
mechanism and splitting them would have meant eight commits that each left
the system half-fixed.

## The complaint

> "I say, please reduce the audio. It went and tried doing it, but the
> audio actually **increased**."

> "please play this song on Spotify or play this playlist — it's not being
> able to do this anymore … I see it pop up with the approve message. But I
> click approve, it doesn't go through with it."

## What happened

Four faults in a row, each of which turned a recoverable misunderstanding
into a silent wrong action.

1. **`companion_manager.py::_tool_desktop_action`** — the live model sends
   arguments as a JSON *string*. When it used Python quotes, or wrapped the
   JSON in a sentence, `json.loads` raised and `except ValueError:
   arguments = {}` threw the arguments away without a word.

2. **`agents/control/registry.py::run`** — an argument whose name the action
   did not recognise was `pop`ped off the dict and the action ran anyway.
   The dropped names were recorded in `Outcome.data` and never read.

3. **`apps/media.py` and `apps/windows_os.py`** — `volume(direction="up")`
   and `media(what="play_pause")`. With 1 or 2 having removed the argument,
   the default ran. So "reduce the audio" performed `volume(direction="up")`
   and got louder, and a lost argument on "next song" paused the music.
   `Param.choices` was declared on both and **never checked**.

4. **`ai/gemini_realtime.py::_run_tool`** — logged `ok` unless the result
   had an `"error"` key, which `desktop_action` never sets. Every one of
   the failures above appears in `tora.log` as a success, which is why the
   problem survived several sessions.

## What happens now

`agents/control/args.py` is new and owns the whole contract.

**`read_arguments(raw)`** (DC-05, DC-06) recovers what the model meant:
strict JSON · Python literals · JSON inside prose · `key=value` pairs · a
single bare value. When it genuinely cannot, it returns the reason and the
action does not run — the model gets a sentence naming the parameters and
can correct itself.

**`bind(key, params, arguments)`** (DC-07, DC-08, DC-10) produces the exact
call or raises `BindError`:

* an unknown name is refused, with the nearest real one suggested
  (`directoin` → "did you mean 'direction'?") and the full list of what the
  action takes;
* `choices` is enforced, through a synonym table first, so *lower*,
  *quieter*, *reduce*, *softer*, *decrease* and *"down a bit"* all mean
  `down`, and *sideways* is refused;
* `Param.type` is coerced once, centrally — `"4"` reaches the action as
  `4`, and `"loads"` is refused.

**No default may choose the verb** (DC-09). `volume(direction)` and
`media(what)` are now required with no default. `Param.modifier=True`
marks the rarer kind that only changes *how* (an editor chat's `mode`),
where a default is safe. A test walks the live registry and fails on any
new action that gets this wrong.

**The log tells the truth** (DC-01, DC-02). `_outcome_of` reports
`ok` / `failed: …` / `denied: …` / `waiting for the user to approve` from
what the result actually says. `agents.control` now logs one line per run
with the action key, the tier and the elapsed time, and a warning naming
any refused argument.

## Surface

| File | Change |
|---|---|
| `agents/control/args.py` | new — `read_arguments`, `bind`, `BindError`, synonyms |
| `agents/control/registry.py` | `run` binds instead of dropping; logs every outcome |
| `agents/control/model.py` | `Param.modifier`, and the rule written down |
| `agents/control/apps/media.py` | `volume(direction)` required |
| `agents/control/apps/windows_os.py` | `volume(direction)`, `media(what)` required |
| `agents/control/apps/editors.py` | `chat(mode)` marked `modifier` |
| `companion_manager.py` | parse failure returns an error, never `{}` |
| `ai/gemini_realtime.py` | `_outcome_of` — the log stops claiming success |

## Out of scope, deliberately

* **DC-12** — the model is given the schema to retry with, but is not yet
  *instructed* to retry automatically; today it will usually ask the user.
* **DC-13** — approval still blocks the model for the length of the dialog.
  That is the "it gets stuck" half of the field report and is wave 3.
* **DC-24** — nothing verifies the action had an effect. `ok` still means
  "the call returned", only now it cannot mean "we ran something else".

## Proof

`tests/agents/test_control_arguments.py`, 33 tests. The ones that matter:

* `test_reduce_the_volume_never_increases_it`
* `test_a_missing_direction_is_refused_not_guessed`
* `test_no_action_may_default_its_direction` — walks the **live** registry,
  so a new action cannot reintroduce the bug
* `test_every_way_of_saying_down_means_down` (8 phrasings)
* `test_the_arguments_the_model_meant_are_recovered` (6 malformed shapes)
* `test_a_failed_tool_call_is_not_logged_as_ok`

619 passing overall.
