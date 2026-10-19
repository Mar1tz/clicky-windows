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

import json
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


# WR-05: several files of one kind in ONE hidden copy of the app. Starting
# Word costs 2-4 s, and it was started once per file. Each file is reported
# on its own line - "ok<TAB>out" or "fail<TAB>in<TAB>why" - and one that fails
# is tried again on its own (with LibreOffice after).
_OPEN = {
    "word": ("$a = New-Object -ComObject Word.Application; $a.Visible = $false; $a.DisplayAlerts = 0",
             "$d = $a.Documents.Open($j.in, $false, $true, $false); $d.ExportAsFixedFormat($j.out, 17); $d.Close(0)",
             "$a.Quit()"),
    "excel": ("$a = New-Object -ComObject Excel.Application; $a.Visible = $false; $a.DisplayAlerts = $false",
              "$b = $a.Workbooks.Open($j.in, 0, $true); $b.ExportAsFixedFormat(0, $j.out); $b.Close($false)",
              "$a.Quit()"),
    "powerpoint": ("$was = @(Get-Process POWERPNT -ErrorAction SilentlyContinue).Count -gt 0; "
                   "$a = New-Object -ComObject PowerPoint.Application",
                   "$s = $a.Presentations.Open($j.in, -1, 0, 0); $s.SaveAs($j.out, 32); $s.Close()",
                   "if (-not $was) { $a.Quit() }"),
}


def _batch_script(app: str) -> str:
    start, one, stop = _OPEN[app]
    # Windows PowerShell hands back the JSON list as ONE array: @() round it
    # made a list of one list, and every path went to Open at once.
    return ("$ErrorActionPreference = 'Stop'\n"
            "$jobs = $env:TORA_LIST | ConvertFrom-Json\n"
            f"{start}\n"
            "try {\n"
            "  foreach ($j in $jobs) {\n"
            f"    try {{ {one}; \"ok`t\" + $j.out }}\n"
            "    catch { \"fail`t\" + $j.in + \"`t\" + $_.Exception.Message }\n"
            "  }\n"
            f"}} finally {{ {stop} }}\n")


# WR-06: no Office app and no LibreOffice is said in a moment, not after
# PowerShell has tried. The COM class an app registers is how Windows knows it.
_PROGID = {"word": "Word.Application", "excel": "Excel.Application", "powerpoint": "PowerPoint.Application"}


def has_office(app: str) -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, _PROGID[app] + r"\CLSID"):
            return True
    except OSError:
        return False
    except Exception:  # noqa: BLE001 - not Windows: let PowerShell say
        return True


_APP_NAME = {"word": "Word", "excel": "Excel", "powerpoint": "PowerPoint"}


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


def _hidden(cmd: list[str], env: dict, timeout: float = TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def to_pdfs(paths: list[str]) -> tuple[list[str], list[str]]:
    """Several files: (the PDFs made, "name: why" for each that was not).
    Files of one kind share one hidden copy of their app (WR-05). Blocking."""
    made: list[str] = []
    failed: list[str] = []
    groups: dict[str, list[str]] = {}
    for path in paths:
        groups.setdefault(app_for(path), []).append(path)
    for app, group in groups.items():
        retry = group
        if app and len(group) > 1 and has_office(app):
            jobs = [{"in": os.path.abspath(p), "out": os.path.abspath(pdf_path(p))} for p in group]
            done_out: set[str] = set()
            try:
                done = _hidden(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                                "-ExecutionPolicy", "Bypass", "-Command", _batch_script(app)],
                               dict(os.environ, TORA_LIST=json.dumps(jobs)), TIMEOUT_S + 30 * len(group))
                for line in (done.stdout or "").splitlines():
                    if line.startswith("ok\t"):
                        done_out.add(os.path.normcase(line[3:].strip()))
            except (subprocess.TimeoutExpired, OSError) as e:
                _log.info("one %s session for %d files failed (%s) - one at a time", app, len(group), e)
            retry = []
            for job, path in zip(jobs, group):
                if os.path.normcase(job["out"]) in done_out and os.path.isfile(job["out"]) \
                        and os.path.getsize(job["out"]) > 0:
                    made.append(job["out"])
                else:
                    retry.append(path)
        for path in retry:
            try:
                made.append(to_pdf(path))
            except ConvertError as e:
                failed.append(f"{os.path.basename(path)}: {e}")
    return made, failed


def to_pdf(path: str) -> str:
    """Make the PDF; its path. Blocking - call it in a thread."""
    app = app_for(path)
    if not app or not os.path.isfile(path):
        raise ConvertError(f"{os.path.basename(path)} is not a document, spreadsheet or presentation")
    if not has_office(app) and not _soffice():
        raise ConvertError(f"{_APP_NAME[app]} is not installed on this PC, and neither is LibreOffice")
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
