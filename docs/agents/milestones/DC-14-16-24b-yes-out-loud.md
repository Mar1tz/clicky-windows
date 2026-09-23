# DC-14, DC-16, and the rest of DC-24 — yes out loud, and permissions that last

Wave 3 continued, plus the honesty gap the previous milestone left open.

## DC-24b · "Next track" can now be proven

The previous pass verified volume and mute and admitted it could not
verify transport: proving a track skipped means knowing what was playing
*before*, and `verify` only ever saw the arguments.

`Action.snapshot` is the missing half. An optional coroutine, called
before `run`, whose result is handed to `verify` as `before=`. Transport
actions sample now-playing, and `system.track_changed` compares after a
short settle — the app needs a moment to rewrite its window title.

A media key tap that nothing listened to is now reported as `ok` but
**unverified**, not as success. When the track does change, the message
becomes what is actually playing, so Tora says "Playing: Michelle" rather
than "pressed next".

A snapshot that fails never stops the action; it just costs the proof.

## DC-14 · Approving by voice

A voice assistant that needs a mouse click to say yes is not hands-free.

The machinery was already there — `manage_agents` has had `approve` and
`deny` since M26, and `pending_approvals()` includes desktop
confirmations. The only thing missing was the model **knowing** something
was waiting, so a spoken "yes" was answered as if it were a new question.

The per-turn context now carries one line when a confirmation is
outstanding: what it is, and that yes/no should call `manage_agents`
instead of being answered. A SENSITIVE request says the opposite — it can
only be approved by typing its word in the dialog, and the model is told
never to approve it on the user's behalf.

## DC-16 · A standing yes outlives the session

`_desktop_grants` was a set in memory. "Always allow" meant "until you
restart", so every session asked again about the same track skip, and a
dialog you see every day is one you learn to click past without reading —
which is the opposite of what it is for.

Grants now live in `desktop_grants.json` next to the log, in plain text,
because a permission you cannot see is not one you can take back.
`desktop_grants()` lists them and `revoke_desktop_action()` withdraws one.
A corrupt file reads as "ask me about everything again": failing open
would grant permissions nobody gave.

### A real mistake, worth writing down

The first test to call `allow_desktop_action` wrote a genuine
`always allow spotify.next` into the user's own profile, because the
default path is the real data directory and nothing in the suite said
otherwise. It then leaked into the next test, which is how it was noticed.

The file was deleted and `tests/agents/conftest.py` now points
`TORA_GRANTS_FILE` at `tmp_path` for every test, autouse. CLAUDE.md
already said tests must never touch the user profile; adding a feature
that writes there is exactly how that rule gets broken without anyone
deciding to break it.

## Surface

| File | Change |
|---|---|
| `agents/control/model.py` | `Action.snapshot` |
| `agents/control/registry.py` | samples before, passes `before=` to verify |
| `agents/control/system.py` | `track_changed` |
| `agents/control/apps/windows_os.py`, `apps/media.py` | transport verified |
| `agents/manager/agent_manager.py` | grants on disk, list, revoke |
| `companion_manager.py` | `_pending_confirmation_line` |
| `tests/agents/conftest.py` | grants isolated per test |

## Out of scope

* **DC-15** the dialog's wording is still `spotify.next - Next track
  (system)` rather than "Skip to the next track in Spotify."
* **DC-19** the dialog is not yet explicitly raised above the overlay.
* Revoking is an API, not a screen: the Settings page lists no grants yet.
* Play/pause is still unverifiable from a window title — it reports itself
  as unverified, which is honest.

## Proof

`test_control_arguments.py` (45) · `test_control_desktop.py` (17):

* `test_next_track_is_proven_by_the_track_changing`
* `test_a_media_key_nothing_listened_to_is_not_a_success`
* `test_a_snapshot_that_breaks_does_not_stop_the_action`
* `test_always_allow_survives_a_restart`
* `test_a_standing_yes_can_be_taken_back`
* `test_a_corrupt_grants_file_means_ask_me_again`
* `test_a_pending_confirmation_is_offered_to_the_voice`
* `test_a_sensitive_confirmation_is_never_approved_by_voice`

636 passing.
