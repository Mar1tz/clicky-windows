# Write for me: Tora types into the box you are in

**Asked for on 10 Oct 2026.** You talk about what you want written, loosely,
the way you would explain it to someone. Tora works out what you mean,
writes it properly, and puts it in whatever text box you are typing in:
an email, a chat, a form, a document, or a prompt. More detail is coming
from the owner; this is version 1.

## How it works

| step | where | what |
|---|---|---|
| 1. Hear it | the live voice | Ctrl+Win. The voice decides between dictation, a command and a question; dictation goes to `write_for_me(request)` with everything you said. |
| 2. The box | `ai/write_for_me.focused_box` | Windows UI Automation reports what has the keyboard focus: its kind and name, the window, and the text already in it. Buttons, lists and read-only fields are not boxes. A password box is never written into. With no box, Tora asks you to click into one. |
| 3. The writing | `write_for_me.compose` | One call to `Models.WRITER` (gemini-3.6-flash, `GEMINI_WRITER_MODEL`) with your words, the box, its text, and a picture of the window, so "reply to this" works. No picture is taken of a sensitive window. It writes only the text: no preamble or quotes, and a `[placeholder]` for any fact you did not give. |
| 4. Into the box | `write_for_me.put` | It waits for the talk shortcut to be let go, then pastes with Ctrl+V. Every clipboard format is put back afterwards; this was tested on the owner's clipboard holding a picture. The pasted text is kept out of Windows' clipboard history. It never presses Enter. If the box lost the focus, the text is left on the clipboard instead. |

The voice is not handed the text and only says that it is in.
`tora.log` gets one line per use: `write for me: N chars into <kind> '<name>' in '<window>' - pasted (written in N s)`.

## Decided with the owner (10 Oct, from their spec)

* **One shortcut, Ctrl+Win**, as for talking. Tora decides first between
  answering, dictation and a command, and never mixes them.
  * **Dictation** (`write_for_me`): describing, explaining or composing;
    "write", "type", "say", "tell them".
  * **Command**: "open", "go to", "search for", "send", "run", "create a
    file", "remind me". Commands are handed to Tora's `desktop_action` and
    `delegate_task`, which ask before anything runs.
  * "Write an email saying I'll be late" is dictation; "send an email
    saying I'll be late" is a command. When it's unclear, it is dictation
    if a text box has focus; otherwise Tora asks. Nothing runs on a guess.
* **The writer checks again:** it returns `kind` (dictation or command)
  before any text. A command is never typed; it goes back to the voice.
  Live on the spec's five examples: 4/5 at first. "Send an email…" was
  typed because a box was open; the prompt now says an instruction to act
  is a command even then.
* **Inserted straight away.** The written text also goes on Tora's card,
  with its Copy button, and Ctrl+Z undoes it.
* **Automatic detection only**: no separate type/do shortcuts.

## Not yet (for the owner's further information)

* **Changing what was written:** "make it shorter", "more formal". Today
  that writes new text after the old.
* **A hotkey of its own**, held to dictate straight into the box (as
  Wispr Flow does), without going through the conversation.
* **The user's own style:** examples of how they write, from a skill.
* **Selected text:** replacing a selection instead of adding after it.

Tests: `tests/agents/test_e2e_write_for_me.py`.
