# HeyClicky's onboarding, and what HeyTora takes from it

From a 6-minute screen recording of HeyClicky's whole onboarding on Windows
(heyclickyonboarding.mp4, 29 Oct 2026). Frames are in
`docs/ui/heyclicky/onboarding/`. The rules for our own onboarding stay in
[DESIGN.md](DESIGN.md) §10; this document is the reference for changing it.

**What makes theirs feel real:** almost every step is the product working,
not a description of it. It hears you, then learns your name from what you
said. It interviews you out loud and remembers the answers. It answers a
real question about your screen, drives your mouse to fill in a list, and
ends by handing you jobs it has already prepared from what you told it. The
text on each screen is short, and the voice does the talking.

---

## Their flow, step by step

Every step is the same dark panel in the middle of the screen. Across the
top are **Back**, step dots, a ♪ sound switch and **Skip demo**. Under that
come a title, one or two grey lines, the live thing, and **Continue**, which
stays greyed out until the step is done. The notch at the top of the screen
shows the state all the way through, with a coloured glow that moves:
**Thinking** (purple), **Speaking** (orange) and **Listening** (teal).

| # | Their screen | What happens | Frame |
|---|---|---|---|
| 0 | The notch: "Okay, let me show you around · I'm sorry my CEO yaps so much · ▶ Show me" | It starts from the notch, in its own voice and with a joke | `00` |
| 1 | **First, can I hear you?** | Bars that move with your voice (they turn red as it hears you). A hint bubble: "Testing, testing, 1, 2, 3". The microphone as a chip you can change. Continue lights up once it has heard you | `01` |
| 2 | **This is how you talk to me** | Big Ctrl / Alt keycaps that light up while held, and "and say 'My name is Dylan'". You say your name and it uses it from then on | `02` |
| 3 | **I'm curious about you.** "Four quick questions about you" | A **spoken interview** with their mascot. Its lines appear as chat bubbles while it speaks. A big status button shows "HeyClicky is speaking…", "Thinking…", "Got it…". You answer by holding the keys, or in an **"Or type here"** box with a mic button. It asks what you work on, your story, what you hope it brings, and whether you've used an AI agent. It reflects each answer back ("So you're juggling code and running a business"), then sums up and thanks you by name | `03`–`06` |
| 4 | **Here's how I'd help you** "I see what you see. Hold the keys, ask, and watch your screen." | A suggested question, **made from your interview**. You ask it for real; the panel steps aside while it answers out loud about your actual screen (the notch says Speaking for almost a minute) | `07` |
| 5 | **Circle anything, ask about it** | A looping **video** showing it done. Under it: Hold Ctrl + Alt and ✎ "highlight anything, then ask". Listening / thinking / speaking shows under the keys as you try it | `08` |
| 6 | **I can drive your computer too** "Ask me to do it and I'll take the mouse while you watch. Move it anytime and it's yours again." | A small **"Your list" window** inside the panel. You say "Please drive my computer and make a list", and it types three items and ticks two off ("driving… press esc to take it back") | `09`, `10` |
| 7 | **Not in the mood to talk? Type** | A Ctrl keycap: "tap it twice, quickly". You type, and the reply comes back in the notch ("Hi Dylan! Your message came through loud and clear") | `11`, `13` |
| 8 | **There's one more thing…** | Their red cursor, big, and **Show me**. It talks while it makes **jobs from your interview**. "Ready to start" cards fly to the top right of the screen ("A practical developer outreach playbook", "Find a lighter editing workflow"), then land on the notch's Home ("Start it from your notch after the tour") | `12`, `14`, `15` |
| 9 | **Plans** | The plan card over everything: Free / Pro / Max | `16` |
| — | Home | "Afternoon, Dylan." The newest ready job, with the job cards beside the notch | `15` |

![Can I hear you](heyclicky/onboarding/01-can-i-hear-you.jpg) ![Talk](heyclicky/onboarding/02-this-is-how-you-talk.jpg)

![Interview](heyclicky/onboarding/03-interview-speaking.jpg)

![Help](heyclicky/onboarding/07-how-id-help-you.jpg) ![Drive](heyclicky/onboarding/10-drive-list-done.jpg) ![Type](heyclicky/onboarding/11-type-double-ctrl.jpg)

![Jobs ready](heyclicky/onboarding/14-jobs-ready-to-start.jpg) ![Home](heyclicky/onboarding/15-notch-home-ready-card.jpg)

---

## Ours today (DESIGN.md §10), next to theirs

| Ours | Theirs | Note |
|---|---|---|
| Sign in | (before the recording) | Stays |
| Welcome: the website's five scenes, moving | 0: the notch, "let me show you around" | Theirs starts from the product itself |
| What you do: name field, work chips, skill cards | 2 (your name, said out loud) and 3 (spoken interview) | Theirs asks the same things, by voice, and uses them |
| Privacy: skip private windows | – | Ours is a real control; it stays |
| Test your microphone | 1 | Same idea; theirs is calmer and lights up as you talk |
| Test your shortcut | 2 | Theirs combines it with learning your name |
| Ask Tora about this (the equation) | 4 | Theirs uses the person's own screen and a question made from the interview |
| Nice job (what we measured) | – | |
| Pick her voice | – | Ours; it could stay |
| Make her yours: colour, start with Windows | – | Could move to after onboarding |
| You're set | 8 + 9: prepared jobs, then Plans | Theirs ends with something to do |
| – | 5: circle anything | We have pointing and tracing, but no step for it |
| – | 6: drive your computer | We have "take over and do it", but no step for it |
| – | 7: Ctrl Ctrl to type | Built today, but no step for it |

---

## A proposal

The same story as theirs, told by Tora, in our design: the onboarding
panel grows out of the pill like the shape does, in our colours, light or
dark.

1. **Sign in** (as now).
2. **The pill speaks:** "Let me show you around" and ▶ Show me.
3. **First, can I hear you?** Our mic test, restyled: bars in our blue,
   the hint bubble, the mic as a chip.
4. **This is how you talk to me:** hold Ctrl + Win and say your name.
   This replaces both the name field and the separate shortcut test.
5. **I'm curious about you:** a short spoken interview, with Tora's own
   cursor character as the face. The answers fill her memory and pick the
   starter skills (they replace the work chips).
6. **Here's how I'd help you:** a question made from the interview, asked
   about their real screen (this replaces the equation).
7. **Circle anything, ask about it:** tracing while asking, tried for real.
8. **I can drive your computer too:** our own small list window that she
   fills in under "take over and do it", with Esc to stop.
9. **Not in the mood to talk? Type:** Ctrl, Ctrl.
10. **One more thing:** jobs prepared from the interview, as "Ready to
    start" cards landing on Home.
11. **Pick her voice**, then **Plans**, then Home.

Privacy stays (between 3 and 4 or after 9), and "Make her yours" moves to
Settings.

## Decided (29 Oct 2026)

- **Onboarding is free.** Its voice turns use an uncounted "onboarding"
  pass on the server (a migration and a function change, built and tested
  against the local Supabase, released with the app). Without it, a spoken
  interview could use much of a Free plan's 50 credits.
- **Her face is Tora's own cursor:** the blue pointer and its "Tora"
  bubble that already lives on screen. It breathes, speaks and points in
  the interview, and it's what people see every day after.
- **Of ours, only "Pick her voice" stays.** The welcome scenes, the
  privacy step (Skip private windows stays on by default, in Settings →
  Privacy) and the "Nice job" screen go.
- **Circle anything is a live demo:** Tora's cursor traces on the panel
  and she asks about it, then they try it. Nothing to film.

## Build order

Each phase is usable on its own; the old onboarding stays until the last.

| Phase | What |
|---|---|
| A | The frame: the panel grows out of the pill like the shape does. Back, step dots, a sound switch, Skip demo. The pill's state glow while she thinks (violet), speaks (amber) and listens (teal), in our palette |
| B | Show me around · Can I hear you? · This is how you talk to me (hold the keys, say your name) |
| C | The free onboarding pass (server, local first) · I'm curious about you: the spoken interview, Tora's cursor as its face, answers into her memory and the starter skills |
| D | Here's how I'd help you (a question from the interview, on their screen) · Circle anything (live) · I can drive your computer (our own list window) · Type: Ctrl, Ctrl |
| E | One more thing: jobs from the interview as "Ready to start" cards on Home · Pick her voice · Plans · the old onboarding removed |
