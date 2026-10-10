# Write for me: Tora types into the box you are in

**Asked for on 10 Oct 2026.** You talk about what you want written, loosely,
the way you would explain it to someone. Tora works out what you mean,
writes it properly, and puts it in whatever text box you are typing in:
an email, a chat, a form, a document, or a prompt. More detail is coming
from the owner; this is version 1.

## How it works

| step | where | what |
|---|---|---|
| 1. Hear it | the live voice | Saying "write a reply saying…", "type out my idea for…" or "draft a post about…" makes the voice call `write_for_me(request)`, passing everything you said, in your words. |
| 2. The box | `ai/write_for_me.focused_box` | Windows UI Automation reports what has the keyboard focus: its kind and name, the window, and the text already in it. Buttons, lists and read-only fields are not boxes. A password box is never written into. With no box, Tora asks you to click into one. |
| 3. The writing | `write_for_me.compose` | One call to `Models.WRITER` (gemini-3.6-flash, `GEMINI_WRITER_MODEL`) with your words, the box, its text, and a picture of the window, so "reply to this" works. No picture is taken of a sensitive window. It writes only the text: no preamble or quotes, and a `[placeholder]` for any fact you did not give. |
| 4. Into the box | `write_for_me.put` | It waits for the talk shortcut to be let go, then pastes with Ctrl+V. Every clipboard format is put back afterwards; this was tested on the owner's clipboard holding a picture. The pasted text is kept out of Windows' clipboard history. It never presses Enter. If the box lost the focus, the text is left on the clipboard instead. |

The voice is not handed the text and only says that it is in.
`tora.log` gets one line per use: `write for me: N chars into <kind> '<name>' in '<window>' - pasted (written in N s)`.

## Not yet (for the owner's further information)

* **Changing what was written:** "make it shorter", "more formal". Today
  that writes new text after the old.
* **A hotkey of its own**, held to dictate straight into the box (as
  Wispr Flow does), without going through the conversation.
* **The user's own style:** examples of how they write, from a skill.
* **Selected text:** replacing a selection instead of adding after it.

Tests: `tests/agents/test_e2e_write_for_me.py`.
