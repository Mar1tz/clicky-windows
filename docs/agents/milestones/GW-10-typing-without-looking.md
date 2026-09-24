# GW-10 — a typing step finishes on the typed text, read without looking

**Status: done.** 20 new tests, 797 passing. `focused_value` checked
read-only against a real focused field.

## The problem

A step that asks for text had two ways to finish: a signal the planner
guessed — usually the next step's target, often already on screen and so
muted — or the vision model comparing two screenshots. UI Automation
already knows what is in the focused field, locally and instantly.

## The trap

The field's value changes on the **first keystroke**. "Value changed" alone
would advance the guide at the *y* of *youtube.com*, while the person is
still typing. So a typing step is done only when the field holds something
**new** — non-empty, different from when the step began — **and has
stopped changing** for 1.5 s.

## What was built

* **`hybrid_pointer.focused_value()`** — the text in the focused field
  through its ValuePattern. A password field is **never read, not even its
  length**; the check returns before the value is touched.
* **`verification.TypedText`** — the finish line described above. Arms only
  when a field is focused at the start of the step.
* **`verification.AnyOf`** — several finish lines for one step, each armed
  on its own, first one crossed wins. The typed text sits **alongside** the
  step's other signals rather than replacing them. (The first draft only
  used it when a step had no other signal; that would almost never have run,
  because typing steps nearly always carry the next step's target, usually
  already visible and muted. Caught on re-reading, before applying.)
* **`_is_typing_step`** — type / enter / write / paste / fill in / input.
  A step that *also* says press Enter or submit is left to what that causes
  (a page loading, a search running): the value settling first would advance
  before they pressed it.

## Proof

`tests/agents/test_e2e_guide_typing.py` (20):

* `test_typing_is_not_finished_while_the_person_is_still_typing` — every
  keystroke of "youtube.com" at a normal pace, none advances
* `test_it_finishes_once_the_text_has_settled`
* text already there, a cleared field and no field are not "typed"
* `test_a_password_field_is_never_read` — asserts the value was never
  accessed
* eight phrasings, including "Entertain yourself" (not a typing step) and
  "Type … and press Enter" (Enter is its finish line)
* a typing step gets the check alongside its planner signals; a click step
  does not
* `AnyOf`: first crossed wins, nothing crossed is not done, one stale part
  does not mute the others
