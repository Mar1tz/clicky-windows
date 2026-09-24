# UC-04b — "open Calculator and do 5 × 5"

**Status: done and demonstrated.** 715 passing.

The question was whether the discovered-action machinery could actually
carry a two-part request end to end. It can, and proving it found two
bugs that would have made most of UC-02's 223 apps unusable.

## The demonstration

```
1. Calculator is open
2. pressed 5 x 5 =
3. Calculator: Display is 25
4. closed
```

Every step went through the ordinary registry — `calculator.open`,
`click_five`, `click_multiply_by`, `click_five`, `click_equals`,
`read_text`. **There is no Calculator code anywhere in Tora.** The thirty
actions it offers were read off the window: `click_seven`,
`click_square_root`, `click_memory_add`, `click_percent`, and so on.

## Bug 1 — every Store app was invisible to UC-04

Calculator, Mail, Camera, Photos and Clock do not run in their own
process: their window belongs to `ApplicationFrameHost.exe`. UC-04
identified apps by executable — deliberately, because executable names do
not change with the machine's language — and then dropped
`ApplicationFrameHost` as a host process with no app in it.

The result was absurd: UC-02 had just made 223 apps *openable*, and the
77 packaged ones among them could not then be *used*.

A window hosted by a frame process is now named by the tail of its title,
which for a packaged app is the app's own name. Ordinary apps are
untouched and still keyed on their executable.

Critically, this produces the **same id as the launcher**: `Calculator` →
`calculator` from both providers, so the app that UC-02 opens is the app
UC-04 can act on. There is a test asserting exactly that, because two
providers quietly disagreeing about an app's name is the kind of fault
that looks like "it just doesn't work".

## Bug 2 — asking about an unknown app returned everything

`actions(app="photoshop")` resolved to nothing, and nothing meant "no
filter", so it returned **every action of every app**. The model reads
that as "here is what Photoshop can do" and picks one belonging to
something else. It now returns nothing.

## The addition — `read_text`

Calculator names its display control *"Display is 0"*, and *"Display is
25"* after the sum. So the action derived from that name changes every
time the number does, and nothing can reliably address it.

Every app now has a `read_text` action: the labels in its window, read
back. It is `PASSIVE`, needs no knowledge of any app, and is the generic
form of *"did that work?"* — which is what makes a two-part request like
this one answerable rather than merely performable.

## Proof

`test_control_windows_ui.py`, 28 tests, including:

* `test_a_store_app_is_named_by_its_title_not_its_host`
* `test_the_app_id_matches_the_one_the_launcher_gives`
* `test_any_app_can_be_asked_what_it_says`
* `test_asking_about_an_unknown_app_returns_nothing_not_everything`
