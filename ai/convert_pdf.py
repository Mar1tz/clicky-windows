"""
A Word, Excel or PowerPoint file to PDF, in the background - no window.

11 Oct 2026: "turn this Word document into a PDF", then "make sure it
converts it in the background". The agent sent for it opened a visible
PowerShell window and went looking round the screen for a minute. A PDF is
one call to the app that made the file: Word, Excel or PowerPoint, driven
through COM from PowerShell with no window - theirs stays as it was - and
the PDF lands next to the original with the same name. LibreOffice, if it
is there, when Office is not. The original is opened read-only and never
changed.

Paths reach PowerShell through environment variables, never pasted into
the script, so a name with quotes in it cannot break or bend the command.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

_log = logging.getLogger("tora.convert")

TIMEOUT_S = 120

_WORD = (".doc", ".docx", ".docm", ".rtf", ".odt", ".txt")
_EXCEL = (".xls", ".xlsx", ".xlsm", ".csv", ".ods")
_POWERPOINT = (".ppt", ".pptx", ".pptm", ".odp")

# Each opens the file read-only, saves the PDF, closes it and quits only the
# copy it started. Word and Excel get their own hidden copy from
# New-Object; PowerPoint runs once per user, so if it was already open it is
# left open.
_SCRIPTS = {
    "word": r"""
$ErrorActionPreference = 'Stop'
$w = New-Object -ComObject Word.Application
try {
  $w.Visible = $false; $w.DisplayAlerts = 0
  $d = $w.Documents.Open($env:TORA_IN, $false, $true, $false)
  $d.ExportAsFixedFormat($env:TORA_OUT, 17)
  $d.Close(0)
} finally { $w.Quit() }
""",
    "excel": r"""
$ErrorActionPreference = 'Stop'
$x = New-Object -ComObject Excel.Application
try {
  $x.Visible = $false; $x.DisplayAlerts = $false
  $b = $x.Workbooks.Open($env:TORA_IN, 0, $true)
  $b.ExportAsFixedFormat(0, $env:TORA_OUT)
  $b.Close($false)
} finally { $x.Quit() }
""",
    "powerpoint": r"""
$ErrorActionPreference = 'Stop'
$was = @(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count -gt 0
$p = New-Object -ComObject PowerPoint.Application
try {
  $s = $p.Presentations.Open($env:TORA_IN, -1, 0, 0)
  $s.SaveAs($env:TORA_OUT, 32)
  $s.Close()
} finally { if (-not $was) { $p.Quit() } }
""",
}


class ConvertError(RuntimeError):
    """Why it could not be made - said to the user as it is."""


def app_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return ("word" if ext in _WORD else "excel" if ext in _EXCEL else
            "powerpoint" if ext in _POWERPOINT else "")


def can_convert(paths: list[str]) -> bool:
    return bool(paths) and all(os.path.isfile(p) and app_for(p) for p in paths)


def pdf_path(path: str) -> str:
    """Beside the original, the same name - "(2)" rather than overwrite one."""
    stem = os.path.splitext(path)[0]
    out, n = stem + ".pdf", 2
    while os.path.exists(out):
        out, n = f"{stem} ({n}).pdf", n + 1
    return out


def _soffice() -> str:
    for candidate in (shutil.which("soffice"), r"C:\Program Files\LibreOffice\program\soffice.exe",
                      r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def _hidden(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def to_pdf(path: str) -> str:
    """Make the PDF; its path. Blocking - call it in a thread."""
    app = app_for(path)
    if not app or not os.path.isfile(path):
        raise ConvertError(f"{os.path.basename(path)} is not a document, spreadsheet or presentation")
    out = pdf_path(path)
    env = dict(os.environ, TORA_IN=os.path.abspath(path), TORA_OUT=os.path.abspath(out))
    why = ""
    try:
        done = _hidden(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                        "-ExecutionPolicy", "Bypass", "-Command", _SCRIPTS[app]], env)
        why = (done.stderr or done.stdout or "").strip().splitlines()[-1:] or [""]
        why = why[0][:200]
    except subprocess.TimeoutExpired:
        why = f"it took over {TIMEOUT_S} s"
    except OSError as e:
        why = str(e)
    if not (os.path.isfile(out) and os.path.getsize(out) > 0):
        soffice = _soffice()
        if soffice:
            _log.info("office could not convert %s (%s) - LibreOffice", os.path.basename(path), why)
            try:
                folder = os.path.dirname(os.path.abspath(path))
                made = os.path.join(folder, os.path.splitext(os.path.basename(path))[0] + ".pdf")
                if not os.path.exists(made):
                    _hidden([soffice, "--headless", "--convert-to", "pdf", "--outdir", folder, path], dict(os.environ))
                    if os.path.isfile(made) and os.path.getsize(made) > 0:
                        return made
            except (subprocess.TimeoutExpired, OSError) as e:
                why = str(e)
        raise ConvertError(f"it could not be converted ({why or 'no Office or LibreOffice found'})")
    return out
