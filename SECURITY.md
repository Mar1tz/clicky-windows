# Security

## Reporting a problem

If you find a security problem in HeyTora, report it privately to the
HeyTora team rather than posting it anywhere public. Include what you found,
how to reproduce it, and what it could let someone do.

## Scope

In scope:
- The HeyTora Windows application (`main.py` and everything it bundles)
- The installer (`Setup-HeyTora.exe`)
- How API keys and the `.env` file are handled
- The network requests made by the AI, web-search and agent modules

Out of scope:
- Third-party services (Google Gemini, DuckDuckGo, Tavily)
- The user's own operating-system configuration
- Anything that needs physical access to the machine

## How HeyTora is built to be safe

- **No telemetry.** HeyTora sends nothing home. Usage stays on your machine.
- **Keys stay local.** API keys live in `.env` in the install folder and are
  only ever sent to the service they belong to. Logs are scrubbed of anything
  key-shaped before they are written (`logsafe.py`).
- **Every model call is disclosed.** Tora's voice, vision and walkthrough
  planning go to Google Gemini over TLS; agents use Gemini too, unless you
  connect another endpoint in Voice & models > Agent endpoints. A screenshot is sent only when a question needs one; the per-turn screen
  context is control names and window titles - never pixels or typed text -
  and the privacy guard withholds screenshots of password managers, banking
  pages and private windows.
- **Nothing runs on your desktop without asking.** Agent actions that change
  files, run commands or control the mouse and keyboard ask first, unless you
  turn on Settings > Approvals; payments, filings and sending messages always
  ask, and Esc takes the mouse and keyboard back.
- **No auto-update.** HeyTora never downloads updates by itself.
