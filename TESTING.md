# Tora â€” Feature Testing Guide

How to verify every feature works. Go top-to-bottom; each section is independent.

---

## 0. Prerequisites

```bash
python main.py
```

âœ… Blue dot appears in system tray (bottom-right of taskbar)  
âœ… `Tora is running` toast notification appears  
âœ… Blue triangle overlay floats next to your cursor  

---

## 1. Push-to-Talk (PTT)

1. Hold **Ctrl + Win**
2. Say *"What page am I on?"*
3. Release the keys

âœ… Overlay waveform animates while you speak  
âœ… Overlay switches to "thinking" spinner  
âœ… Tora describes the current page/app  
âœ… TTS speaks the answer  

---

## 2. Wake Word

1. Do NOT press any hotkey
2. Say **"Tora"** then immediately *"what time is it?"*

âœ… Blue buddy reacts after you say "Tora"  
âœ… Tora answers the question  

---

## 3. Esc to Stop

1. Hold **Ctrl + Win**, ask *"explain the entire history of the internet"* (long answer)
2. Wait 2 seconds into the response
3. Press **Esc**

âœ… TTS stops immediately  
âœ… Overlay returns to idle state  

---

## 4. Pixel-Perfect Pointing

> No screenshot should be taken for this — the search box has an accessibility name

1. Open any browser to google.com
2. Hold **Ctrl + Win**, say *"where is the search bar?"*

âœ… Blue buddy flies via bezier arc to the search bar  
âœ… Pulsing highlight ring appears around it  
âœ… Speech bubble shows "search bar"  
âœ… Tora says *"That's the Google search bar"* (or similar)  
âœ… Buddy returns to cursor after TTS ends  

**Also test:**
- *"where is the sign in button?"*
- *"where is the address bar?"*

---

## 5. Slow Mode (Teacher Pace)

1. Right-click tray â†’ **Tutor Mode â†’ Slow Mode: OFF** â†’ turns ON
2. Ask *"where is the search bar?"* again

âœ… Flight arc is noticeably slower (~2.5s vs ~1.5s)  
âœ… Buddy dwells longer before returning  

3. Turn Slow Mode back OFF

---

## 6. Multi-Step Lesson

1. Open a video in VLC or any video player
2. Ask *"how do I take a screenshot in Windows?"*

âœ… Tora gives Step 1, ends with *"say 'next' when ready"*  
3. Say **"next"**  
âœ… Step 2 delivered without a new LLM call  
4. Continue until done  

---

## 7. Repeat Command

1. Ask any question (e.g., *"what is on my screen?"*)
2. Wait for it to finish speaking
3. Say **"repeat"** or **"say that again"**

âœ… Tora replays the last TTS without querying the LLM again  

---

## 8. Web Search

1. Ask *"what is the weather in Mumbai today?"* or *"who won the last IPL match?"*

âœ… Panel shows `[1]`, `[2]` citation references  
âœ… Answer reflects current real-world data (not 2023 training cutoff)  

---

## 9. Conversation Route + Latency

1. Right-click tray → the info line should read **Gemini · realtime**
2. Hold **Ctrl + Win**, say *"what is a taskbar?"*, release

✅ First spoken word within about a second of you stopping — no "Thinking…" pause
✅ Panel fills with the transcript as it speaks
✅ Ask three more questions in a row — none of them reconnects (log has one
   "realtime session open" line for the whole run)

3. Tray → **Conversation** → pick **Cascade**, ask the same question

✅ Info line reads **Gemini · cascade**
✅ Noticeably slower (several seconds) — this is the fallback route

4. Switch back to Realtime

---

## 10. Pointing Without Looking

1. Open Notepad, hold the hotkey, say *"where is the Format menu?"*

✅ Pointer lands on the Format menu
✅ `%LOCALAPPDATA%\Tora\tora.log` shows a `UIA hit` line and **no**
   "look" / vision call for this turn

2. Open a game or an image, say *"what's the best move here?"*

✅ Tora says something like "let me look" first, then answers
✅ Log shows a deep-look call — this is the one that is meant to be slow

---

## 11. Panel UI

1. Right-click tray â†’ **Show Panel**

âœ… Panel appears bottom-right  
âœ… Provider badge shows active provider  
âœ… Model dropdown has correct models  
âœ… Status dot and label match current state  

2. Ask a question while watching panel  
âœ… Response streams into the panel text area  
3. Click **â€”** button  
âœ… Panel hides  
4. Double-click tray icon  
âœ… Panel reappears  

---

## 12. Drag & Drop Document Context

1. Show Panel (tray â†’ Show Panel)
2. Find any PDF or DOCX file in Explorer
3. Drag it onto the Tora panel

âœ… Toast: *"Document Attached"*  
4. Ask *"summarise what's in the document I just gave you"*  
âœ… Tora summarises the file contents  

**Alternative:** Tray â†’ Journal â†’ Attach documentâ€¦ â†’ pick a file  

---

## 13. Knowledge Journal

1. Have a 3â€“4 question conversation with Tora
2. Say *"what did we cover today?"*

âœ… Tora summarises today's Q&A from the local journal  

3. Say *"what did we cover this week?"*  
âœ… Weekly digest  

**Check the database:**
```
%LOCALAPPDATA%\Tora\journal.db
```

---

## 14. Quiz Mode

1. Open a website or document with visible content
2. Tray â†’ **Tutor Mode â†’ Quiz Mode: OFF** â†’ turns ON
3. Hold **Ctrl + Win**, say *"quiz me"*

âœ… Tora asks YOU a question about what's on screen  
âœ… Answer it â€” Tora evaluates in one sentence  
âœ… Next question follows automatically  

4. Turn Quiz Mode OFF  

---

## 15. Code Mode

1. Open VS Code or any IDE
2. Tray â†’ **Tutor Mode â†’ Code Mode (auto): ON**
3. Ask *"explain what this code does"*

âœ… Response uses code blocks with language tags  
âœ… Explanation is more technical / step-by-step  

---

## 16. Multilingual

1. Tray â†’ **Tutor Mode â†’ Multilingual: ON**
2. Ask a question in Hindi: *"à¤®à¥‡à¤°à¥€ à¤¸à¥à¤•à¥à¤°à¥€à¤¨ à¤ªà¤° à¤•à¥à¤¯à¤¾ à¤¹à¥ˆ?"*

âœ… Tora detects Hindi  
âœ… Responds in Hindi  
âœ… TTS voice switches to a Hindi voice  

3. Try French: *"qu'est-ce qu'il y a sur mon Ã©cran?"*  
âœ… Same behaviour in French  

---

## 17. OCR Fallback

> Requires Tesseract binary installed

1. Open a page with small/dense text (e.g., a legal document, footnotes)
2. Ask *"read the fine print"* or *"what does the small text say?"*

âœ… Tora runs OCR on the screenshot  
âœ… Extracts text the vision model might have missed  

---

## 18. Whiteboard Annotations

1. Ask a question where Tora would point at multiple things, e.g.:
   *"show me where the menu bar and the address bar are"*

âœ… Arrows or circles drawn on screen  
âœ… Annotations fade out after ~4 seconds  

---

## 19. Lesson Recording

1. Tray â†’ **Lesson Recording â†’ Start recording**

âœ… Toast: *"Recording to: C:\Users\...\recordings\lesson_....mp4"*  

2. Ask 2â€“3 questions  
3. Tray â†’ **Lesson Recording â†’ Stop recording**  

âœ… Toast: *"Lesson saved"*  
4. Open `%LOCALAPPDATA%\Tora\recordings\`  
âœ… MP4 file exists  
âœ… `_transcript.md` file exists with all Q&A  

---

## 20. Workflow Capture

1. Tray â†’ **Workflow Capture â†’ Start capturing my clicks**

âœ… Toast: *"Recording your clicks + keysâ€¦"*  

2. Do 5â€“10 actions: click around, type something, switch tabs
3. Tray â†’ **Workflow Capture â†’ Stop + send to Tora**
4. Ask *"what did I just do?"*

âœ… Tora narrates your workflow step by step  

---

## 21. Privacy Guard

1. Tray â†’ **Tutor Mode â†’ Privacy Guard: ON** (should be on by default)
2. Open KeePass, Bitwarden, or any app with "login" / "password" in the title
3. Ask Tora anything

âœ… Tora says it skipped the screenshot for privacy  
âœ… No screenshot taken of your password manager  

---

## 22. Per-App Memory

1. Ask Tora something in Chrome: *"what's on screen?"*
2. Switch to VS Code
3. Ask: *"what were we just talking about?"*

âœ… Tora has separate context â€” it won't mention the Chrome content  
âœ… Each app has its own conversation history  

---

## 23. Skills System

1. Copy `skills/example_self_mode.py` to `~/.tora/skills/my_skill.py`
2. Restart Tora
3. Say the trigger phrase from the skill

âœ… Skill fires its custom handler  
âœ… Custom response returned  

---

## 24. Voice Picker

1. Say *"change voice to Jenny"*

âœ… TTS voice switches to `en-US-JennyNeural`  

2. Ask a question  
âœ… New voice speaks the answer  

---

## 25. Tray Journal Folder

1. Tray â†’ **Journal â†’ Open journal folder**

âœ… Explorer opens `%LOCALAPPDATA%\Tora\`  
âœ… You can see `journal.db` and recordings  

---

## 26. Agent Workforce

Autonomous agents that do work in the background while you keep talking.
Automated coverage: `python -m pytest tests/agents -q` (440+ tests, offline).
This section is the manual pass.

**Setup:** any one of these makes the agents think:
- your existing Google key (nothing to do), or
- Tray → Agent settings… → Connected accounts… → add an OpenAI-compatible
  endpoint, click **Test** (must say `OK — N models`), Save.

1. Say *"Create a new folder called MyApp and build the foundation of a React application inside it"*

✅ Tora replies "Understood… I will: 1. Create project folder …" and keeps talking
✅ Tray menu shows **Agents… (1 running)**
✅ Tray → Agents… opens a card: green dot, *Application Builder*, a filling bar, `Step 4/10`
✅ Within ~10 s the card says **Done**; `Documents\Tora Projects\MyApp\src\App.jsx` exists

2. Click **Logs** on the card

✅ Timeline shows `Task started` → `Plan: 10 steps` → per step: `create_file(...)`, `Created App.jsx`, `Verified step`
✅ Quit Tora, restart, open the timeline again → identical history (from SQLite)

3. Tray → Agent settings… → *Run without asking, up to* **Safe** → Save. Repeat step 1.

✅ An approval dialog appears for the first file write, showing the tool, path, risk and workspace
✅ **Deny** → task fails with "permission denied"; **Approve** → continues; **Always allow** → no more prompts for that tool

4. Say *"Build me a complete website for my bakery"*

✅ Panel shows *Project Lead* with three indented cards: Backend Agent, Frontend Agent, Testing Agent
✅ Backend and Frontend run at the same time; Testing starts only after both finish

5. While an agent is running, press **Esc** (or Tray → Stop all agents)

✅ The task is cancelled; the card shows **Stopped**

6. Start a task, then Quit Tora mid-run. Restart.

✅ The card comes back **Paused** with "interrupted by restart"; **Resume** finishes only the remaining steps

7. Say *"Build me a CRM application"* (needs a connected provider)

✅ A plan of ~10 real steps (backend, pages, routing, git) — not a template

8. Open `%LOCALAPPDATA%\Tora	ora.log`

✅ No `key=AIza…` anywhere (search for `AIza`); credentials read `REDACTED`

---

## 27. Accounting Extension

No credentials needed - everything runs on the demo client.

1. Tray → **Extensions…** → Enable **Accounting**. The card turns ON.
2. Tray → **Dashboard…** → **Demo client**. "ABC Manufacturing (Demo)" appears with period 2026-08, ZAR.
3. **Month-end close** → an orb appears, finishes in a few seconds. **Needs attention** lists ~27
   exceptions, including "Supplier payment R84,200.00 to Steelco Supplies has no matching invoice".
   **Reports** shows the management pack, variance, bank rec and `Exceptions.md`. Month-end bar: 100%.
4. **Process inbox** → Drafts shows a draft bill for Acme AP-7790 (status draft) and BrightSpark
   (status review); the FastFreight duplicate and "New supplier BrightSpark Electrical" are exceptions.
5. Select the Acme draft → **Post selected…** → an approval dialog (POST) → Approve → status posted.
6. **Payables run** → a payment batch draft; the duplicate Acme bill is held. Select it →
   **Release payment batch…** → the dialog shows SENSITIVE and Approve stays greyed out until you
   type RELEASE. There's no "allow for this task" button.
7. Voice / text box: "prepare month-end for ABC" → routed to the Month-End Agent. "what is a
   month-end?" → answered, no task.
8. Say "I'm an accountant" with the pack off → Tora offers it; "yes" → it turns on and the dashboard opens.
9. **New agent…** → Accounts Payable, weekdays 08:00 → it's listed under **Agents** with a next run time.
10. Tray → Extensions… → Disable → the Accounting tab and tools are gone; the client folder is untouched.

Automated: `python -m pytest tests/agents -q` (framework, pack, spreadsheet, UI tests included).

## Quick Smoke Test (5 minutes)

Run this sequence to verify core features fast:

```
1. python main.py                    â†’ tray icon appears
2. Hold hotkey â†’ "what's on screen?" â†’ answer spoken
3. Esc during response               â†’ stops immediately
4. Say "Tora, where is [element]"  â†’ buddy flies to it
5. Tray â†’ Quiz Mode ON â†’ "quiz me"   â†’ quiz starts
6. Tray â†’ Quiz Mode OFF
7. Drag a PDF onto panel             â†’ toast confirms attach
8. Ask "summarise the document"      â†’ summary spoken
9. Say "what did we cover today?"    â†’ journal summary
10. Tray â†’ Quit Tora               â†’ clean exit
```

All 10 steps passing = Tora is fully functional.
