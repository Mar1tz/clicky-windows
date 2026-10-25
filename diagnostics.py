"""The diagnostics package, redacted, shown before it goes anywhere (EN-16).

"Save diagnostics report" used to write a file and open it. Now the package
is built here - versions, models, memory counts and the last lines of
tora.log - and everything in it is redacted: keys and tokens (logsafe and
devlink's rules), the Windows user name in paths, email addresses. The
person sees exactly this text in a preview and only then saves it; nothing
is ever sent from here.
"""
from __future__ import annotations

import datetime
import getpass
import os
import platform
import re
import sys
from pathlib import Path

LOG_LINES = 200
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def redact(text: str) -> str:
    """Secrets, the user name and email addresses out of `text`."""
    try:
        import logsafe
        text = logsafe.redact(text)
    except Exception:  # noqa: BLE001
        pass
    try:
        from devlink.redact import redact as code_redact
        text = code_redact(text)
    except Exception:  # noqa: BLE001
        pass
    text = _EMAIL.sub("<email>", text)
    for name in {getpass.getuser(), os.environ.get("USERNAME", "")} - {""}:
        # Any run of separators: a path in a logged repr has its backslashes doubled.
        text = re.sub(rf"(?i)([\\/]+users[\\/]+){re.escape(name)}\b", r"\1<you>", text)
    home = os.path.expanduser("~")
    if home and home != "~":
        text = text.replace(home, "<home>")
    return text


def _log_tail(log_dir: Path, lines: int = LOG_LINES) -> list[str]:
    path = log_dir / "tora.log"
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-lines:]
    except OSError:
        return ["(no tora.log)"]


def build(log_dir: Path) -> str:
    """The whole package, redacted - what the preview shows and what is saved."""
    import traceback
    report = [f"HeyTora diagnostics — {datetime.datetime.now().isoformat(timespec='seconds')}"]
    try:
        from version import __version__
        report.append(f"HeyTora: {__version__}")
    except Exception:  # noqa: BLE001
        pass
    report.append(f"Python: {sys.version.split()[0]}")
    report.append(f"Platform: {platform.platform()}")
    try:
        from config import cfg
        d = cfg.describe()
        report.append(f"Active LLM: {d.get('llm', '?')}")
        report.append(f"STT: {d.get('stt', '?')}  TTS: {d.get('tts', '?')}")
        report.append(f"Conversation route: {d.get('conversation', '?')}  Voice: {d.get('voice', '?')}")
    except Exception:  # noqa: BLE001
        report.append(traceback.format_exc())
    report += ["", "─── Models ───"]
    try:
        from ai.gemini import Models
        for role, model in Models.all_roles().items():
            report.append(f"  {role:<12} {model}")
    except Exception:  # noqa: BLE001
        report.append(traceback.format_exc())
    report += ["", "─── Memory ───"]
    try:
        from ai import gemini_memory
        report.append(str(gemini_memory.stats()))
    except Exception:  # noqa: BLE001
        report.append(traceback.format_exc())
    report += ["", f"─── tora.log, last {LOG_LINES} lines ───", *_log_tail(log_dir)]
    return redact("\n".join(report))


def save(text: str, folder: Path) -> Path:
    out = folder / f"diagnostics-{datetime.datetime.now():%Y%m%d-%H%M%S}.txt"
    out.write_text(text, encoding="utf-8")
    return out
