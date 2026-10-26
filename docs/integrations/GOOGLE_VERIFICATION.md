# Google OAuth verification — HeyTora (Gmail send + Calendar read)

What Google asks for before HeyTora's Google sign-in works for the public
without the "unverified app" warning (plan item CI-16). Both scopes HeyTora
uses are **sensitive**, not restricted: a manual review, **no** paid security
assessment. Reading the inbox (`gmail.readonly`) would be restricted and need
a yearly CASA assessment — HeyTora deliberately does not ask for it.

Expect: brand check 2–3 business days; scope review about 3–5 business days
on Google's target, 1–3 weeks in practice with one or two rounds of
questions. Until then the app can be published unverified for up to 100
users (with the warning).

## 1. Before submitting

| Item | What | Where it goes |
|---|---|---|
| Domain | A domain you own, verified in Google Search Console with the same Google account as the Cloud project | Search Console → Add property |
| Homepage | `https://<domain>/` — says what HeyTora is, that it is a Windows app, links the privacy policy | OAuth consent screen → App domain → Application home page |
| Privacy policy | `https://<domain>/privacy` — section below, word for word or close | App domain → Privacy policy link |
| Terms | `https://<domain>/terms` (optional but recommended) | App domain → Terms of service |
| Logo | 120×120 PNG, the same mark as the app | Branding → App logo |
| Authorized domain | `<domain>` | Branding → Authorized domains |
| Support email | an address that is read | Branding |
| Scopes | `openid`, `email`, `https://www.googleapis.com/auth/gmail.send`, `https://www.googleapis.com/auth/calendar.readonly` | Data Access |
| Client | OAuth client of type **Desktop app** (its id/secret go in HeyTora as `TORA_GOOGLE_CLIENT_ID` / `TORA_GOOGLE_CLIENT_SECRET`) | Clients |

## 2. Privacy policy — the Google section

> **Google account data.** If you connect a Google account, HeyTora asks only
> for the permissions a feature you use needs:
>
> - *Send email on your behalf* (`gmail.send`) — used only to send a message
>   you have reviewed and approved in HeyTora. HeyTora cannot read, search or
>   delete your email.
> - *See your calendar events* (`calendar.readonly`) — used only to answer
>   your questions about your schedule. HeyTora cannot create, change or
>   delete events.
>
> Your Google sign-in tokens are stored on your PC in Windows Credential
> Manager and are never sent to HeyTora's servers. Message and event content
> is processed on your PC and, when you ask Tora about it, sent to the AI
> model that answers you; it is not stored by HeyTora and not used to train
> models. You can disconnect at any time in HeyTora (which revokes the
> access) or at https://myaccount.google.com/permissions.
>
> **Limited Use.** HeyTora's use and transfer of information received from
> Google APIs adheres to the
> [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
> including the Limited Use requirements.

## 3. Scope justifications (paste into the form)

**gmail.send** — "HeyTora is a voice assistant for Windows. When the user asks
it to send an email ('email Lee that I'm running late'), HeyTora drafts the
message and shows the user the full message — sender, recipients, subject,
body — in an approval window. Only after the user approves that exact
message does HeyTora send it with gmail.send. Any change to the message
requires a new approval. gmail.send is the narrowest scope that allows
sending; HeyTora does not read the mailbox."

**calendar.readonly** — "When the user asks about their schedule ('what's on
tomorrow', 'am I free Friday afternoon'), HeyTora reads the events in that
date range from the primary calendar to answer. It never creates, changes or
deletes events, so read-only access is sufficient."

## 4. Demo video (unlisted YouTube, ~2 minutes)

1. Title card: "HeyTora — Google OAuth demo". Show the consent screen settings
   page with the app name and the two scopes (5 s).
2. Start HeyTora on Windows. Say: "email Lee that I'm running late".
3. Tora says Gmail is not connected and offers to connect; say "yes".
4. The browser opens Google's consent screen — **show the URL bar with the
   client id** and the permission list ("Send email on your behalf"). Approve.
5. Back in HeyTora: the approval window shows From / To / Subject / Body.
   Click Approve. Tora says "Sent". Show the message in the recipient's inbox.
6. Edit flow: ask again, change the body when asked; show that a new approval
   appears.
7. Say "what's on my calendar tomorrow"; connect Calendar the same way
   (consent screen visible), hear the answer.
8. Disconnect: show that HeyTora revokes access, then show
   myaccount.google.com/permissions without HeyTora.

## 5. After approval

Ship the client id/secret with the app (they are not secrets for a Desktop
client) instead of `.env`, and remove the 100-user unverified warning from
the release notes.
