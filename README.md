# HeyTora for Windows 🔵

> **An AI teaching companion that lives next to your cursor.**
> Ask it anything about your screen — it points, explains, and guides you step-by-step, like a real tutor sitting beside you.

Built with **Python 3.11+ and PyQt6**, runs fully in the background, and talks to you in real time over Google's Gemini Live API.

---

## Hi, this is Tora 👋

[![Watch the demo](assets/tora-demo.gif)](https://youtu.be/WYY9yJHDaEU)

> 🎬 **[Watch full demo on YouTube →](https://youtu.be/WYY9yJHDaEU)**

Tora is a little AI buddy that **lives next to your cursor**. Hold a hotkey, ask it something about your screen, and it talks back — pointing at buttons, walking you through steps, drawing arrows on your screen. Think of it as a patient tutor sitting beside you while you learn anything: video editing, coding, a new app, whatever.

No more Alt-Tab to ChatGPT. No more typing out descriptions of what's on your screen. Just hold **Ctrl + Win**, speak, and Tora handles the rest.

One free [Gemini API key](https://aistudio.google.com/app/apikey) and you're running. Nothing to install locally, no models to download.

**It answers the moment you stop talking** — the websocket is already open, the server decides when your sentence ended, and nothing is uploaded unless the question actually needs a look at your screen.

---

## What it looks like

```
┌─────────────────────────────────────────────┐
│  Your screen (browser, IDE, Premiere, etc.) │
│                                             │
│          🔵◂ ← Tora, your blue buddy      │
│          (floats beside your real cursor)   │
│                                             │
│  ┌───────────────────────────┐              │
│  │  HeyTora [Gemini]     —   │  ← panel     │
│  │  ● Thinking…              │              │
│  │  "The search bar is       │              │
│  │   right here ↗"           │              │
│  │  Model: gemini-3.8-live   │              │
│  └───────────────────────────┘              │
└─────────────────────────────────────────────┘
```

The blue triangle sits **35 px right / 25 px below** your real cursor. When you ask it to point at something, it **flies** there along a smooth bezier arc (teacher pace), dwells with a pulsing highlight ring, then flies back.

---

## Feature List

### 🎙️ Voice Activation
- Hold **Ctrl + Win** and speak — or tap it once and talk; Tora answers when you stop
- The mic stays closed until you press it. For hands-free, turn on the **"Tora"** wake word from the tray
- Press **Esc** to stop any response or TTS mid-stream

### 👁️ Screen Aware
- Every question comes with one line about the screen: the app, the window
  title, what has focus and every control Tora can point at by name —
  straight from Windows, no pixels sent
- A real look at the screen (`look_at_screen`) only when the answer needs it:
  a game, a chart, a chessboard, a diagram, an unlabelled error
- Describes only what it sees — never hallucinates
- Detects active window title for per-app memory

### 🎯 Pixel-Perfect Pointing
- **Windows UI Automation first** — the accessibility tree already knows where
  every button is, to the pixel, and answers in about 5 ms. Tora tells the
  model the names of the controls on screen, so "where's the Save button?"
  needs no screenshot at all.
- **Offline OCR** for canvas apps that expose no tree (Figma, Photoshop, games)
- **The vision model** last, for anything neither of those can find — one call,
  natively grounded boxes, no grid tricks
- Bezier arc flight with configurable teacher pace
- Pulsing highlight ring + speech bubble label on the target
- Works on any DPI scale (4K, HiDPI, multi-monitor setups)

### 🧑‍🏫 Real Tutor Behaviour
- **Locate queries** → points + 1-sentence explanation (no generic text directions)
- **Multi-step tasks** → breaks into steps, says "Say 'next' when ready"
- **"Next" / "Continue"** → advances the lesson without a new LLM call
- **"Repeat"** → replays the last answer via TTS without re-querying
- **"Stop" / "Cancel"** → cancels generation instantly

### 🌐 Web Search (Real-Time Data)
- DuckDuckGo HTML scrape + concurrent page fetch — **no API key needed**
- Optional Tavily upgrade for deeper results
- Responses grounded in today's data with `[1]`, `[2]` citations
- Always knows today's date — never gives stale answers

### ⚡ One Model Per Job

Every part of HeyTora runs on the Gemini model that suits it, and the model ids
are all overridable in `.env`.

| Where | Model | What it does |
|---|---|---|
| Realtime | `gemini-3.8-live` | The conversation itself. Audio in, audio out, and all the tool calling — this is what you talk to on the hotkey. |
| Planner | `gemini-3.1-pro-preview` | Breaks "show me how to…" into steps with targets and completion conditions. Deliberate, 10–20 s, which is why Tora says "let me work that out" while it thinks. |
| Vision · fast | `gemini-3.8-flash` | The cheap look. First pass on any screen question, thinking switched off. |
| Vision · deep | `gemini-3.1-pro-preview` | The careful look. Reads chessboards, charts, dense code. Advice-shaped questions go straight here. |
| Speech → text | `gemini-3.5-transcribe-live` | Dictation and wake-word checks. |
| Text → speech | `gemini-3.1-flash-tts-preview` | The cascaded fallback voice — not the one you normally hear. |
| Embeddings | `gemini-embedding-001` | Memory and documents. Recalls "you always use dark mode" and finds the right paragraph of an attached PDF. |

### 🏎️ Why it feels instant

Three deliberate choices, all measured:

1. **The websocket opens at startup**, not on the hotkey. A Live handshake is
   most of a second — paid once per session instead of once per question.
2. **No screenshot unless the question needs one.** Tora sends one line of
   text: the app, the window title, what has focus and every control it can
   point at by name. Most questions are answered from that. A real look at the
   screen happens only when the answer needs pixels.
3. **The server decides when you finished talking.** A local silence timer has
   to wait to be sure and spends that wait every single time. Streaming the
   trailing silence to Gemini instead means it hears the sentence end and is
   already answering: **measured at a few milliseconds after the last word,
   against ~1.4 s for hotkey-driven turn signalling.**

If the websocket can't be opened, Tora falls back to the slower
transcribe → think → speak route automatically and reconnects in the
background. Force it with `TORA_CONVERSATION=cascade` to compare.

### 🧠 Per-App Memory
- Separate conversation history per active application
- Context never bleeds between Premiere, VS Code, Chrome, etc.
- History cap: last 20 messages per app

### 📝 Knowledge Journal + Spaced Repetition
- Every Q&A automatically logged to a local SQLite database
- Ask **"what did we cover today?"** → summary of today's session
- Ask **"what did we cover this week?"** → weekly digest
- **SM-2 spaced repetition** — Tora reminds you to review topics at optimal intervals (1 → 3 → 7 → 14 → 30 → 60 → 120 days)
- Say **"quiz me on what I should review"** → flashcard session from your journal
- Journal stored at `%LOCALAPPDATA%\HeyTora\journal.db`

### 📄 Document Context (Drag & Drop)
- Drag a **PDF, DOCX, TXT, MD, CSV, or code file** onto the HeyTora panel
- It is embedded once with `gemini-embedding-001`; each question then pulls
  back only the passages that relate to it, instead of the whole file
- Or use **Tray → Journal → Attach document…** for a file picker
- Supports multi-file: attach several docs and ask cross-document questions

### 🔍 OCR Fallback (Fine Print)
- When your query mentions "fine print", "small text", "read that", etc., Tora runs **Tesseract OCR** on the screenshot
- Extracts text too small or low-contrast for the LLM's vision to read
- Requires the Tesseract binary from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
- Gracefully skipped if Tesseract isn't installed

### 💻 Code Mode (Auto-Detect)
- Detects VS Code, Cursor, IntelliJ, PyCharm, Vim, Neovim, Sublime, etc. by window title
- Automatically injects a **code-specialist system prompt addendum**: prefers code blocks, includes language tags, explains algorithms step by step
- Toggle: **Tray → Tutor Mode → Code Mode (auto)**

### 🎓 Tutor Modes (Tray → Tutor Mode)

| Toggle | What it does |
|---|---|
| **Slow Mode** | 1.7× slower bezier flight + longer dwell — easier to follow |
| **Quiz Mode** | Tora asks *you* questions instead of answering; evaluates in one sentence |
| **Privacy Guard** | Skips the screenshot when a password manager / banking window is detected |
| **Code Mode (auto)** | Code-specialist prompt when an IDE is active |
| **OCR Fallback** | Runs Tesseract on screenshots when fine text is mentioned |

### 🎬 Lesson Recording (MP4 + Transcript)
- Records the screen at 8 fps as an MP4 alongside a Markdown transcript of all Q&A
- Start: **Tray → Lesson Recording → Start recording**
- Stop: tray menu → produces `lesson.mp4` + `transcript.md`
- Saved to `Documents\Tora Lessons\<date and time>\`

### 🖊️ Whiteboard Annotations
Tora can draw directly on your screen as it explains:
- `[ARROW:x1,y1->x2,y2]` — animated arrow between two points
- `[CIRCLE:x,y,r:label]` — pulsing circle with optional label
- `[UNDERLINE:x,y,w]` — underline beneath text
- `[LABEL:x,y:text]` — floating text label

Annotations fade out after ~4 seconds and clear automatically on the next query.

### 🖱️ Workflow Capture
- Records your mouse clicks and keystrokes while you work
- Start: **Tray → Workflow Capture → Start capturing my clicks**
- Stop: **Tray → Workflow Capture → Stop + send to Tora**
- Ask **"What did I just do?"** → Tora narrates your workflow step-by-step
- Requires `pynput` (included in `requirements.txt`)

### 🎤 Voice Picker
- **Tray → Voice** — twelve prebuilt Gemini voices: Zephyr, Puck, Charon,
  Kore, Fenrir, Leda, Orus, Aoede, Callirrhoe, Autonoe, Enceladus, Iapetus
- The same voice is used for the realtime conversation and the fallback route
- Or set `GEMINI_TTS_VOICE` in `.env`

### 🔒 Privacy Guard (on by default)
Detects sensitive windows and skips the screenshot entirely:
- Password managers: KeePass, Bitwarden, 1Password, LastPass, Authenticator
- Sensitive pages: `login`, `sign in`, `banking`, `.env` files, `credit card`

### 🛠️ Skills System (User-Extensible)
Create your own voice triggers — no pull request needed:

```python
# ~/.tora/skills/my_skill.py
import asyncio

SKILL = {
    "name": "open_calculator",
    "trigger": r"(open|launch|start) calculator",
    "description": "Opens Windows Calculator",
    "handler": open_calc,
}

async def open_calc(transcript: str, manager) -> str:
    import subprocess
    subprocess.Popen("calc.exe")
    return "Opening calculator for you."
```

Place skill files in `~/.tora/skills/` — Tora auto-loads them on startup.

---

## Installation

### Prerequisites
- Windows 10 / 11 (64-bit)
- Python 3.11, 3.12, or 3.14
- A working microphone
- A free [Gemini API key](https://aistudio.google.com/app/apikey)

### 1. Get the code
Open a terminal in the project folder:
```bash
cd path\to\heytora
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure your API key

**Installed a release build?** You don't need to touch a file — open the tray
icon → **Setup & Diagnostics → API Keys…**, paste your key, and hit Save. The
first-run wizard offers the same screen and checks the key works. Keys are
written to `.env` next to `HeyTora.exe` for you.

**Running from source?** Create `.env` in the project root (copy from
`.env.example`):

```env
# ── The one required setting ──
# Free key: https://aistudio.google.com/app/apikey
GOOGLE_API_KEY=AIza...

# ── Web search (optional — falls back to DuckDuckGo) ──
TAVILY_API_KEY=...

# ── Customise ──
TORA_HOTKEY=ctrl+win
GEMINI_TTS_VOICE=Zephyr
TORA_CONVERSATION=realtime
```

Every model id is overridable too — see `.env.example` for the full list.

### 4. Run
```bash
python main.py
```
A blue dot appears in your system tray. HeyTora is now running.

---

## Cost

HeyTora is free software; the models are Google's. A [Gemini API
key](https://aistudio.google.com/app/apikey) has a free tier that covers
ordinary use, and the design keeps the expensive calls rare:

- Most questions never send a screenshot at all — the app, window and control
  names come from Windows itself, for nothing.
- The glance (`gemini-3.8-flash`) runs with thinking switched off.
- The careful look and the guide planner (`gemini-3.1-pro-preview`) only run
  when the model decides the question genuinely needs them.
- Attached documents are embedded once, then only the handful of relevant
  passages are sent — not the whole PDF on every question.

Web search uses DuckDuckGo with no key; Tavily is an optional upgrade.

---

## Distribution — Single .exe Installer

For sharing with friends without Python:

```bash
# 1. Generate icon
python assets/make_icon.py

# 2. Build
pip install pyinstaller
pyinstaller heytora.spec --clean --noconfirm

# 3. Distribute
# → dist/HeyTora/  (entire folder)
# → or build Setup.exe with Inno Setup using installer.iss
```

`dist/HeyTora/HeyTora.exe` runs on any Windows machine without Python installed. Include a `.env` file next to the exe with your API keys, or set them as system environment variables.

See [BUILD.md](BUILD.md) for full packaging instructions, including the Inno Setup installer.

---

## Usage

### Voice Commands

| Say | Action |
|---|---|
| *"Where is the search bar?"* | Points at it + 1-sentence explanation |
| *"How do I export this video?"* | Step-by-step lesson mode |
| *"What's on screen?"* | Screen description |
| *"Search for [topic]"* | Web search + cited answer |
| *"next" / "continue"* | Advance to next lesson step |
| *"repeat"* | Replay last answer via TTS |
| *"stop" / "cancel"* | End current lesson |
| *"quiz me"* | Quiz mode on current screen |
| *"what did we cover today?"* | Today's journal summary |
| *"what should I review?"* | Spaced repetition flashcards |
| *"what did I just do?"* | Workflow capture narration |
| *"change voice to Jenny"* | Switch TTS voice |

### Keyboard

| Key | Action |
|---|---|
| `Ctrl + Win` (hold) | Push-to-talk |
| `Esc` | Stop response / TTS immediately |

### Tray Menu
Right-click the tray icon → full menu including:
- Conversation route (realtime / cascade), voice picker, model list
- Tutor Mode submenus
- Lesson Recording controls
- Workflow Capture controls
- Journal (view folder, attach doc)
- Show/Hide Panel

---

## Architecture

```
main.py
  ├── CompanionManager          # async state machine + orchestrator
  │     ├── AmbientListener     # mic, local VAD, wake word, live frame sink
  │     ├── ai.gemini           # model roles + one pooled HTTP/2 connection
  │     ├── ai.gemini_live      # Live API websocket transport
  │     ├── ai.gemini_realtime  # the conversation: gemini-3.8-live + tools
  │     ├── ai.screen_summary   # one line about the screen — no pixels
  │     ├── ai.hybrid_pointer   # UIA tree, then OCR — both local, both fast
  │     ├── ai.gemini_vision    # the glance and the careful look
  │     ├── ai.gemini_planner   # multi-step guides
  │     ├── ai.gemini_memory    # embeddings: facts + document passages
  │     ├── ai.web_search       # DuckDuckGo + Tavily + citation builder
  │     ├── ai.model_registry   # live model list, 7-day cache
  │     ├── screen.capture      # multi-monitor JPEG (physical px + DPI metadata)
  │     ├── tutor.py            # classifiers, privacy guard, window detection
  │     ├── tutor_features/
  │     │     ├── journal.py          # SQLite Q&A log + SM-2 spaced repetition
  │     │     ├── pdf_context.py      # PDF/DOCX/TXT text extraction
  │     │     ├── ocr.py              # Tesseract OCR fallback
  │     │     ├── code_mode.py        # IDE detection + code prompt addendum
  │     │     ├── lesson_recorder.py  # MP4 + transcript recording
  │     │     ├── multilang.py        # langdetect → reply-language directive
  │     │     ├── workflow_capture.py # pynput click/key recorder
  │     │     └── collab.py           # live session skeleton (WebRTC)
  │     ├── skills/             # user-extensible voice triggers
  │     └── audio/              # gemini_stt (dictation), gemini_tts (fallback voice)
  ├── CursorOverlay             # transparent click-through Qt window
  │     ├── Blue triangle buddy (spring-follows cursor)
  │     ├── Bezier arc flight   (teacher-pace pointing)
  │     ├── Highlight ring      (pulsing circle on detected element)
  │     ├── Speech bubble       (label while dwelling)
  │     ├── Whiteboard annotations (arrows, circles, underlines, labels)
  │     ├── Waveform bars       (listening state)
  │     └── Spinner arc         (thinking state)
  ├── CompanionPanel            # optional chat panel with model dropdown
  └── TrayManager               # system tray + full context menu
```

### A turn, end to end

```
hotkey down ─► mic opens, frames start flowing to the ALREADY-OPEN socket
            └► one line of screen context sent alongside (no screenshot)

  "where's the save button?"  ─────────────► gemini-3.8-live
                                                    │
                            point_at("Save") ◄──────┘
                                   │
                      UIA lookup, ~5 ms, exact pixels
                                   │
            arrow flies  ◄─────────┘   and the answer is already being spoken

hotkey up   ─► trailing silence keeps streaming so the server hears the
               question end — it has usually started answering before this
```

### Coordinate Pipeline (Pixel-Perfect Pointing)
```
"where's the Save button?"
        │
        ▼
Tier 1  Windows UI Automation   ~5 ms    the tree knows the exact rectangle
        │ miss
        ▼
Tier 2  Offline OCR             ~300 ms  canvas apps: Figma, games, Java UIs
        │ miss
        ▼
Tier 3  gemini-3.8-flash        ~1 s     one call, natively grounded box
                                         [ymin,xmin,ymax,xmax] 0-1000
        │
        ▼
physical monitor pixels
  → + monitor origin (multi-monitor offset)
  → ÷ DPI scale (HiDPI correction)
  = logical screen pixels
        │
        ▼
overlay.point_at(logical_x, logical_y)  ← correct position on any screen
```

---

## File Structure

```
heytora/
├── main.py                      # entry point — boots Qt, wires all signals
├── companion_manager.py         # async orchestrator + state machine
├── config.py                    # env loading, model roles, prompts
├── tutor.py                     # classifiers, privacy guard, prompt builder
├── hotkey.py                    # global hotkey + Esc stop
│
├── ai/
│   ├── gemini.py                # model roles + the one pooled HTTP/2 client
│   ├── gemini_live.py           # Live API websocket transport
│   ├── gemini_realtime.py       # the conversation: gemini-3.8-live + tools
│   ├── gemini_provider.py       # cascade text/vision streaming (fast ↔ careful)
│   ├── gemini_vision.py         # glance / careful look / locate — grounded boxes
│   ├── gemini_planner.py        # multi-step guides with targets + done-when
│   ├── gemini_memory.py         # embeddings: user facts + document passages
│   ├── screen_summary.py        # one line about the screen, no pixels
│   ├── hybrid_pointer.py        # UIA tree → OCR, both local
│   ├── figure_detector.py       # OpenCV shapes with exact vertices
│   ├── model_registry.py        # live model list, 7-day cache
│   ├── base_provider.py         # BaseLLMProvider ABC
│   └── web_search.py            # DuckDuckGo + Tavily + citations
│
├── audio/
│   ├── ambient_listener.py      # mic, local VAD, wake word, live frame sink
│   ├── capture.py               # PCM capture, noise-gate, auto-gain, trim
│   ├── playback.py              # PCM streaming player + cancellable playback
│   ├── stt/
│   │   └── gemini_stt.py        # gemini-3.5-transcribe-live
│   └── tts/
│       └── gemini_tts.py        # gemini-3.1-flash-tts-preview (fallback voice)
│
├── screen/
│   └── capture.py               # multi-monitor JPEG + DPI metadata
│
├── tutor_features/
│   ├── journal.py               # SQLite Q&A log + SM-2 spaced repetition
│   ├── pdf_context.py           # PDF/DOCX/TXT extraction
│   ├── ocr.py                   # Tesseract OCR fallback
│   ├── code_mode.py             # IDE detection + code prompt addendum
│   ├── lesson_recorder.py       # MP4 + transcript
│   ├── multilang.py             # language detection → reply directive
│   ├── workflow_capture.py      # click/keystroke recorder
│   └── collab.py                # live session skeleton
│
├── skills/
│   ├── __init__.py              # skill loader + trigger matcher
│   └── example_self_mode.py     # example skill
│
├── ui/
│   ├── overlay.py               # CursorOverlay — all drawing + annotations
│   ├── panel.py                 # CompanionPanel — chat + model dropdown
│   ├── tray.py                  # TrayManager — tray icon + full menu
│   └── design.py                # shared colours, fonts, constants
│
├── assets/
│   ├── make_icon.py             # generates icon.ico
│   └── icon.ico                 # (generated)
│
├── .env                         # your API key (not committed)
├── requirements.txt             # all Python dependencies
├── heytora.spec               # PyInstaller build spec
├── installer.iss                # Inno Setup installer script
├── build.bat                    # one-click build script
├── BUILD.md                     # full build + packaging guide
└── LICENSE                      # MIT
```

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_API_KEY` | — | **Required.** Everything runs on it. `GEMINI_API_KEY` accepted as an alias |
| `TAVILY_API_KEY` | — | Tavily search (upgrades DuckDuckGo) |
| `TORA_CONVERSATION` | `realtime` | `realtime` (websocket, ~0.5 s to first word) or `cascade` (transcribe → think → speak) |
| `GEMINI_TTS_VOICE` | `Zephyr` | Prebuilt Gemini voice, used by both routes |
| `GEMINI_REALTIME_MODEL` | `gemini-3.8-live` | The conversation |
| `GEMINI_PLANNER_MODEL` | `gemini-3.1-pro-preview` | Multi-step guide planner |
| `GEMINI_VISION_FAST` | `gemini-3.8-flash` | The glance |
| `GEMINI_VISION_DEEP` | `gemini-3.1-pro-preview` | The careful look |
| `GEMINI_STT_MODEL` | `gemini-3.5-transcribe-live` | Dictation + wake word |
| `GEMINI_TTS_MODEL` | `gemini-3.1-flash-tts-preview` | Fallback voice |
| `GEMINI_EMBED_MODEL` | `gemini-embedding-001` | Memory + documents |
| `TORA_HOTKEY` | `ctrl+win` | Global push-to-talk combo |
| `TORA_MIC_MODE` | `hotkey` | `hotkey` or `ambient` (wake word) |
| `TORA_TAP_MAX_SECONDS` | `0.6` | Tap vs hold threshold |
| `STT_LANGUAGE` | *(empty)* | Pin the transcription language (ISO code, e.g. `de`); empty = auto-detect |
| `RESPONSE_LANGUAGE` | *(empty)* | Pin Tora's reply language; empty = match the user |
| `MIC_DEVICE_INDEX` | *(empty)* | Force a specific input device; empty = system default |
| `CUSTOM_INSTRUCTIONS` | *(built-in prompt)* | Full override of Tora's system prompt — also editable live via Tray |

---

## Troubleshooting

**Tora doesn't hear me**
→ Check the default microphone in Windows Sound settings
→ Pick a different mic under **Tray → Setup & Diagnostics → Microphone**

**Tora hears me but says nothing**
→ Almost always the key. **Tray → Setup & Diagnostics → API Keys…** — the
  first-run wizard checks it; run **Setup again** from the tray to re-check
→ Look in `%LOCALAPPDATA%\HeyTora\tora.log` for a `Gemini 4xx` line —
  it carries Google's actual reason

**Answers take seconds instead of half a one**
→ The tray badge should read **Gemini · realtime**. If it says *cascade*, the
  websocket couldn't be opened — a proxy or firewall blocking `wss://` is
  the usual cause. Tora retries in the background
→ Pointing questions should not trigger a screenshot. If they do, the app in
  front exposes no accessibility names (some games, some Electron apps) and
  Tora is falling back to looking

**Pointing at wrong location**
→ Windows accessibility names come first; check the control has one (most
  do). Canvas apps fall back to OCR, then to the vision model
→ Multi-monitor: the DPI scale is read per monitor — mixed scales work, but
  a monitor added while HeyTora is running needs a restart

**Esc doesn't stop audio**
→ Make sure `sounddevice` is installed (`pip install sounddevice`)
→ Check that no other app has exclusive access to the audio device

**OCR returns nothing**
→ Install the Tesseract binary from https://github.com/UB-Mannheim/tesseract/wiki
→ Add `C:\Program Files\Tesseract-OCR\` to your system PATH

**Lesson recording fails**
→ `pip install imageio imageio-ffmpeg`
→ ffmpeg is bundled with imageio-ffmpeg — no separate install needed

**Panel doesn't appear**
→ Right-click the tray icon → Show Panel
→ Or double-click the tray icon

---

## Credits
- Models: [Google Gemini](https://ai.google.dev/) — the Live API for the conversation, plus flash/pro for vision, TTS for the fallback voice, and embeddings for memory
- Pointing: Windows UI Automation via [uiautomation](https://pypi.org/project/uiautomation/), OCR via [RapidOCR](https://pypi.org/project/rapidocr-onnxruntime/)
- Free web search: DuckDuckGo via [ddgs](https://pypi.org/project/ddgs/) + [Tavily](https://tavily.com)

---

## License
MIT — see [LICENSE](LICENSE)
