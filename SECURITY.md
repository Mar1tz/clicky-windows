# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.1.x   | :white_check_mark: |
| 1.0.x   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in Tora, please report it responsibly.

**Do NOT open a public issue.** Instead, email:

**shashanksingh2338@gmail.com**

Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Response Timeline

- **Acknowledgment**: within 48 hours
- **Assessment**: within 1 week
- **Fix release**: as soon as a patch is ready (typically within 2 weeks for critical issues)

## Scope

The following are in scope:
- The Tora Windows application (`main.py` and all bundled modules)
- The installer (`Setup-Tora.exe`)
- API key handling and `.env` file processing
- Network requests made by the web search and AI provider modules

The following are out of scope:
- Third-party services (Google Gemini, DuckDuckGo, Tavily)
- The user's local operating system configuration
- Vulnerabilities that require physical access to the machine

## Security Design

- **No telemetry** — Tora sends zero data home. All usage stays on your machine.
- **API keys stored locally** — keys live in `.env` in the install directory, never transmitted except to their intended provider.
- **One provider, clearly disclosed** — every model call goes to Google Gemini over TLS. Screenshots are sent only when a question needs one; the per-turn screen context is control names and window titles, never pixels or typed content.
- **No auto-update** — the app never phones home for updates. Users download new versions manually from GitHub Releases.

Thank you for helping keep Tora safe for everyone.
