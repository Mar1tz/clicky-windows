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

## How it works

`ai/email_reply.py`

1. **`app_for(title)`** picks the email app in front. `GmailWeb` matches
   any browser window whose title says Gmail. Others get "replying works
   in Gmail for now".
2. **`read()`** takes the text of the web page (the document's
   TextPattern, at most 30,000 characters) and your own address from the
   window title.
3. **`compose_reply()`** makes one call to `Models.WRITER`. The newest
   message not from you is the one answered, your instructions come first,
   and the reply matches the email's tone and language. Nothing is
   invented: missing facts become `[confirm date]`. Subjects are cleaned
   from "Re: Re:" to "Re:", any address not in the email is dropped, and
   you are never Cc'd yourself.
4. **`fill()`**:
   * With no reply open, it presses Gmail's own **Reply all** (or
     **Reply**), so Gmail fills To and Cc.
   * A reply you opened keeps the recipients you chose. Tora won't add
     addresses next to recipient chips it can't see for certain.
   * Otherwise it fills only what is empty: the Subject in a pop-out
     reply.
   * The body is pasted at the top (Ctrl+Home), above the quote and the
     signature.
   * The clipboard is put back afterwards, as with dictation.

   Controls are found with Windows' own search (FindAll on a name), which
   takes milliseconds, by Gmail's labels: `Message Body`,
   `To recipients`, `Subject`, `Reply`, `Reply all`.
5. **Privacy:** the email is read only when you ask. Its text goes to the
   writer and is dropped. `tora.log` records only lengths and what was
   filled: `email reply: Gmail, read the page (N chars) in N s, wrote N
   chars, filled reply all, body in N s`.

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

Subclass `MailApp`: implement `matches(title)`, `read()`, `draft_text()`
and `fill(reply)`, then add it to `APPS`.

## Later (the owner will add details)

New emails from scratch, forwarding, several drafts to choose from, and
Outlook.

Tests: `tests/agents/test_e2e_email_reply.py`, against a fake Gmail page.
