# Tora for Windows — Setup Guide

## Prerequisites

- Python 3.11+
- A Google Gemini API key — free from <https://aistudio.google.com/app/apikey>

That's the whole list. Nothing to install locally, no models to download.

## Install

```bash
cd tora-windows
pip install -r requirements.txt
```

## Configure

```bash
copy .env.example .env
```

Open `.env` and paste your key into `GOOGLE_API_KEY`. Everything else in that
file has a working default.

You can also skip this entirely — the first run opens a setup window that
takes the key, saves it and checks it works.

## Run

```bash
python main.py
```

A floating panel appears in the bottom-right corner and the Tora icon
appears in your system tray.

**Hold `Ctrl+Win`** and ask a question. Tora answers when you stop talking.
Tapping the key once works too — it listens until you finish.

Press **Esc** to cut an answer short.

## What runs where

| Job | Model |
|-----|-------|
| The conversation — hears you, calls tools, speaks back | `gemini-3.8-live` |
| Planning a multi-step guide | `gemini-3.1-pro-preview` |
| First look at the screen | `gemini-3.8-flash` |
| The careful look — charts, boards, code | `gemini-3.1-pro-preview` |
| Dictation and wake-word checks | `gemini-3.5-transcribe-live` |
| Fallback voice | `gemini-3.1-flash-tts-preview` |
| Memory and document recall | `gemini-embedding-001` |

Override any of them in `.env` — see `.env.example`.

## How it answers so quickly

Three things, none of which are obvious:

1. **The websocket is opened at startup**, not when you press the hotkey. The
   handshake is most of a second, and it is paid once per session rather than
   once per question.

2. **No screenshot is sent unless it's needed.** Tora tells the model which
   app you're in, what has focus and every button it can point at by name —
   one line of text, straight from Windows. "Where's the Save button?" is
   answered from that, instantly. Only questions that genuinely need pixels
   (a game, a chart, a chessboard) trigger a real look at the screen.

3. **The server decides when you've finished talking.** A local silence timer
   has to wait long enough to be sure, and spends that wait on every single
   question. Gemini hears the sentence end and starts answering immediately —
   measured at a few milliseconds after the last word, against about 1.4
   seconds for hotkey-driven turn signalling.

If the websocket cannot be opened, Tora falls back to the slower route —
transcribe, think, speak — automatically, and reconnects in the background.
Force that route with `TORA_CONVERSATION=cascade` to compare.

## Troubleshooting

Tray → **Setup & Diagnostics → Save diagnostics** writes a report with the
active models, key status and memory stats, and opens it.

The runtime log is at `%LOCALAPPDATA%\Tora\tora.log`.
