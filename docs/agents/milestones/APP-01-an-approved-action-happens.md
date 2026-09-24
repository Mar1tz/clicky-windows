# APP-01 — a desktop action you approve, happens

**Status: done.** 22 new tests, 972 passing.

## The problem

The field log has **12 desktop actions that the user approved and that then
did nothing**. The live model named an argument its own way, and arguments
were only checked when the approved action ran:

| sent | declared | times |
|---|---|---|
| `open_app` with `app_name` | `name` | 8 |
| `open_folder` with `path` | `folder` | 1 |
| `volume` with `level` | `to_percent` | 1 |
| `volume` "set it to 30%" with no direction | `direction` required | 1 |
| `window` with `action` | `command` | 1 |
| `spotify.play` with nothing to play | `query` | 1 |

The error message for `app_name` even said "did you mean 'name'?", and then
refused anyway, after the yes.

## What was built

**Names that can only mean one thing are accepted** (`agents/control/args.py`).
There are two rules, and both are unambiguous by construction:

* an `ALIASES` table built from the field log (`app_name`/`app` → `name`,
  `path` → `folder`, `level`/`percent` → `to_percent`, `action` →
  `command`, `search` → `query`, …), applied only to a parameter the
  action actually declares;
* a name that contains exactly one declared parameter's name
  (`app_name` holds `name`, `folder_path` holds `folder`).

Anything that could mean two parameters means neither. A name given
together with its alias, or two aliases for one argument, is refused.
Truly unknown names are still refused with a suggestion. The rule the
module exists for still holds: an argument is never silently lost, and
nothing is guessed.

**"Set the volume to 30 percent"** needs no direction.
`Param(unless=("to_percent",))` declares that `to_percent` can stand in for
`direction`. With neither, the call is still refused, naming both. The rule
test still forbids any default on a choice, except exactly this declared
"not given". On a machine whose volume cannot be read, a level is refused
rather than nudged: tapping "down" for "set it to 60" would be the
opposite-action bug all over again. 0% is a real target, not "not given".

**Checked before anyone is asked.** `desktop_action` binds the arguments
first. A call that cannot run goes straight back to the model with the
reason, and the user is never shown a dialog for it. The dialog now shows
the arguments as they will really run (`name`, not `app_name`).

## Proof

`tests/agents/test_control_approval_args.py` (22):

* every refusal in the log, replayed against the real declarations;
* 0, 30 and 100 percent are each set exactly;
* a level that cannot be set is refused and never tapped;
* up and down still work;
* unknown names, a name together with its alias, two aliases for one
  argument, and a name that could mean two arguments are all refused;
* an alias never lands on an argument the action does not have;
* a call that cannot run never reaches the approval dialog;
* the dialog shows the canonical arguments.
