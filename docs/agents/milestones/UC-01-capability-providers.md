# UC-01 — capability providers

**Status: done.** 12 tests, 651 passing.

## The problem

An adapter is a file someone wrote about one app. There are eight. This
machine has 223 launchable apps, 298 URI schemes, and 127 addressable
controls in a single File Explorer window. An app nobody wrote a file
about is invisible — and from outside, invisible is indistinguishable from
"Tora cannot do that". That is how a request to play a song became a
browser tab (`DC-21b`).

## What was built

`agents/control/provider.py`:

* **`AppProfile`** — an app discovered rather than declared: id, display
  name, aliases, which provider found it, and an opaque `launch` hint.
* **`CapabilityProvider`** — a protocol with two methods:
  * `apps()` — every app this *mechanism* can reach. Cheap, runs on
    rebuild.
  * `actions(app)` — what that one app can be asked, right now. May be
    slow, runs **only when someone names that app**.

`AppControl` now takes `providers=` alongside `adapters=` and consults
them in `apps()`, `actions()`, `resolve_app()`, `find()` and
`suggestions()`.

## The three rules that make it safe

**Laziness is the whole design.** `apps()` is cheap and eager;
`actions()` is expensive and lazy. Nothing enumerates 223 apps' actions,
ever — that is slower than the conversation, and since the tool payload is
re-billed every turn (M28), dearer than the answer. Tested:
`test_nothing_is_asked_about_until_it_is_named`.

**Answers are cached per app** for `REFRESH_S`, so asking twice costs
once. Tested: `test_an_answer_is_remembered`.

**An adapter always wins.** A synthesised action with the same key as a
hand-written one is dropped: the hand-written one came from someone who
knew that app. Tested: `test_an_adapter_beats_a_provider_for_the_same_app`.

And a provider that raises — listing apps or describing one — is recorded
in `broken()` and costs only its own answer.

Synthesised actions get the full wave-1 argument contract for free: an
unknown parameter name on a provider action is refused exactly as it is on
an adapter's.

## Surface

| File | Change |
|---|---|
| `agents/control/provider.py` | new — `AppProfile`, `CapabilityProvider` |
| `agents/control/providers/__init__.py` | new — `available()`, the one place to register a provider |
| `agents/control/registry.py` | providers consulted throughout; per-app action cache |

## Out of scope

There are **no providers yet** — UC-01 is the socket, not the plug.
UC-02 (launcher) is the first one, and the first time the app list stops
being nine things.
