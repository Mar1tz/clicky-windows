# Write for me: Tora types into the box you are in

**Asked for on 10 Oct 2026.** You talk about what you want written, loosely,
the way you would explain it to someone. Tora works out what you mean,
writes it properly, and puts it in whatever text box you are typing in:
an email, a chat, a form, a document, or a prompt. More detail is coming
from the owner; this is version 1.

## How it works (instant, since the evening of 10 Oct)

The owner: "it mustn't take screenshots and click - it needs to be instant.
HeyClicky's is so fast the person doesn't leave the page; this one takes so
long I leave the page and then it doesn't work." The first version looked
at a picture of the window, waited 2-5 s for the whole text and pasted it.
Now:

| step | where | what |
|---|---|---|
| 1. Hear it | the live voice | Ctrl+Win. The voice decides between dictation, a command and a question; dictation goes to `write_for_me(request)` with everything you said. |
| 2. The box | `write_for_me.focused_box` | Windows UI Automation reports what has the keyboard focus: its kind and name, the window, the text already in it. Buttons, lists and read-only fields are not boxes. A password box is never written into. With no box, Tora asks you to click into one. |
| 3. The writing | `write_for_me.stream_dictation` | The fastest model (`Models.WRITER`, gemini-3.5-flash-lite, no thinking) streams. Its first line is `TYPE`, or `DO: <what>` for a command, which is never typed and goes back to the voice. "Reply to this" reads the page's own text (`page_context`, UI Automation), **never a screenshot**. |
| 4. Into the box | `write_for_me.Typist` | Typed as it is written, a word at a time, as keystrokes in one batched SendInput call per piece. The first words appear about a second after you stop talking. A new line is Shift+Enter, never Enter alone. A code editor (VS Code, Cursor, JetBrains…) gets one paste at the end instead, with the clipboard put back exactly. **If you leave the window or the tab, typing stops** and the rest waits on the clipboard. |

The voice is not handed the text and only says that it is in; the text goes
on Tora's card with its Copy button. `tora.log`: `write for me: N chars into
<kind> '<name>' in '<window>' - typed, first words at N s, all in N s`.

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
* **Inserted straight away**, typed as it is written. The written text also
  goes on Tora's card, with its Copy button.
* **Automatic detection only**: no separate type/do shortcuts.

## Not yet (for the owner's further information)

* **Changing what was written:** "make it shorter", "more formal". Today
  that writes new text after the old.
* **A hotkey of its own**, held to dictate straight into the box (as
  Wispr Flow does), without going through the conversation.
* **The user's own style:** examples of how they write, from a skill.
* **Selected text:** replacing a selection instead of adding after it.

Tests: `tests/agents/test_e2e_write_for_me.py`.
