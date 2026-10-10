# Reply to the email on screen

**Asked for on 10 Oct 2026.** "Look at this email and write a reply", or
"respond saying I'll do it Monday". Tora reads the email you are looking
at, writes the reply, and fills in the reply form: To, Subject, and the
body above the quoted original. **It never sends.** You read it and press
Send.

## Decided with the owner

| | |
|---|---|
| Reading | The page's own text, through Windows UI Automation. That covers the whole thread, the addresses and the subject, even what is scrolled away. A picture of the window is the backup when a page shows no text. |
| Sign-off | Copied from how you sign your own emails in the thread. With none to copy, "Kind regards," and your first name. If the box already holds your signature, none is added. |
| Preview | None: the draft is filled at once, and the reply also goes on Tora's card. |
| Recipients | Reply all when others were on the email; say "just reply to him" to answer only the sender. |
| Starting it | Ctrl+Win, as for everything. Replying is its own mode beside dictation and commands. Even "send a reply" only fills the draft. |

## How it works (instant, since the evening of 10 Oct)

"It mustn't take screenshots and click - it needs to be instant." The
first version waited about 5 s for a reply written whole. Now
(`ai/email_reply.py`, on `ai/uia_page.py`):

1. **`app_for(title)`** picks the email app in front. `GmailWeb` matches
   any browser window whose title says Gmail. Others get "replying works in
   Gmail for now".
2. **At once, side by side:**
   * **`read()`** takes the page's own text (UI Automation, ~0.3 s) and your
     address from the window title. **Never a screenshot**: a page with no
     readable text is said so at once.
   * **`open_reply()`**: with no reply open, Gmail's own **Reply all** (when
     others were on it and you didn't say "just him") or **Reply** is
     pressed through Windows' Invoke. No mouse moves, and Gmail fills To and
     Cc itself. A pop-out reply's empty Subject gets "Re: " plus the window
     title's subject, with no AI. The caret goes to the top of the body,
     above the quote and the signature. A reply you opened keeps the
     recipients you chose.
3. **`stream_reply()`**: the fastest model (`Models.WRITER`, no thinking)
   writes only the body: the newest message not from you is the one
   answered, your instructions come first, the email's tone and language
   are matched, nothing is invented (`[confirm date]`), and you sign off as
   you do in the thread. **It is typed into the body as it is written**
   (`write_for_me.Typist`). If you leave the page, typing stops and the
   rest goes on the clipboard. **It never sends.**
4. **Privacy:** the email is read only when you ask. Its text goes to the
   writer and is dropped. `tora.log` gets lengths and timings only: `email
   reply: Gmail, read the page (N chars) and opened reply all in N s, first
   words at N s, N chars - typed, all in N s`.

## Fixed after the owner's first try (10 Oct, 21:32)

* **The subject.** It was typed: the keystrokes were still arriving when
  the focus moved to the body, so the subject box showed only "Re:" and
  the rest ("Sunflex Ops – additions made along the way") landed at the
  end of the reply. Now the subject is **set** through Windows'
  ValuePattern, with no keystrokes. If an app won't take that, it is typed
  and the box is waited on until it says so. A lone "Re:" counts as empty.
  Every focus change now waits until the box really has the keyboard
  focus (`uia_page.focus`).
* **Speed.** The first words came at 2.4 s. Now the writing starts the
  moment the page is read, while Gmail's reply box is still opening
  (`_prefetched` in `companion_manager`).
* **A crash a minute after a reply.** UI Automation's COM was torn down
  after each use on a worker thread, and the controls made there were
  released into it later. It is now readied once per thread and left
  ready (`uia_page.in_thread`).

## Checked live (10 Oct, on the owner's Gmail in Opera, read-only)

* **Labels:** all found by name. `Reply` (button and link), `Forward`,
  `Message Body` (Edit), `To recipients` (ComboBox), `Subject` (Edit).
  There was no `Reply all` on an email to one person, which is right:
  plain Reply is used.
* **Speed:** the page text took 0.3 s; each control search takes about
  0.1-0.2 s on Gmail's large page.
* **The reply** was written in 4.6 s from the real thread. It went to the
  sender with "Re: <subject>" and the owner's own sign-off ("Kind regards,
  Torben Mikkelsen, Agentora"), and invented nothing. It was not put in
  the draft.

## Adding an app (Outlook next)

Subclass `MailApp`: implement `matches(title)`, `read()` and
`open_reply(reply_all, keyboard)`, then add it to `APPS`.

## Later (the owner will add details)

New emails from scratch, forwarding, several drafts to choose from, and
Outlook.

Tests: `tests/agents/test_e2e_email_reply.py`, against a fake Gmail page.
