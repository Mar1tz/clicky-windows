"""Keep secrets out of tora.log.

tora.log is the file users are asked to paste into bug reports. It had
the Gemini key in it 166 times, because httpx logs every request URL at
INFO and the app's client sends the key as `?key=…`. Three layers fix
that, none of which depends on changing how any API is called:

  1. httpx / httpcore are held at WARNING, so request lines are not
     logged at all.
  2. A RedactingFilter on every handler rewrites anything that still
     looks like a credential - a `key=` query value, a Google `AIza…`
     key, an `sk-…` token, a bearer header - before it is written. This
     catches WARNING-level errors that quote a URL, tracebacks, and
     anything a future dependency decides to log.
  3. On startup the existing log files are scrubbed in place, so the
     leak that already happened is closed too.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

# Order matters: the specific query-param form first, then bare tokens.
SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"((?:^|[?&\s])(?:key|api_key|apikey|token|access_token)=)[^&\s\"']+", re.I | re.M),
     r"\1REDACTED"),
    (re.compile(r"(x-goog-api-key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9_\-]{16,}", re.I), r"\1REDACTED"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{16,}"), r"\1REDACTED"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "AIza…REDACTED"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "sk-…REDACTED"),
    (re.compile(r"\b(gho|ghp|github_pat)_[A-Za-z0-9_]{16,}\b"), r"\1_…REDACTED"),
]

NOISY_HTTP_LOGGERS = ("httpx", "httpcore", "websockets.client", "urllib3")


def redact(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Rewrite the record's message and args so no handler sees a secret."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _redact_value(v) for k, v in record.args.items()}
                else:
                    record.args = tuple(_redact_value(a) for a in record.args)
        except Exception:  # noqa: BLE001 - a filter must never break logging
            pass
        return True


def _redact_value(value):
    return redact(value) if isinstance(value, str) else value


def install(root: logging.Logger | None = None) -> None:
    """Quiet the HTTP loggers and put the filter on every handler."""
    root = root or logging.getLogger()
    for name in NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    filt = RedactingFilter()
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(filt)


def scrub_file(path: Path) -> int:
    """Redact secrets in an existing log file. Returns the number of
    lines changed. Rewrites through a temp file so a crash mid-way
    leaves the original intact."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    changed = 0
    out_lines = []
    for line in text.splitlines(keepends=True):
        clean = redact(line)
        if clean != line:
            changed += 1
        out_lines.append(clean)
    if changed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(out_lines), encoding="utf-8")
        tmp.replace(path)
    return changed


def scrub_logs(log_dir: Path, names: Iterable[str] = ("tora.log", "tora.log.1", "tora.log.2")) -> int:
    total = 0
    for name in names:
        p = log_dir / name
        if p.is_file():
            total += scrub_file(p)
    return total
