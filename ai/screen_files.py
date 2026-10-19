"""
Which files the user means - "this document", "these files" - as real paths.

11 Oct 2026: the owner traced over a Word document in File Explorer and
said "turn this Word document into a PDF". The agent was handed "convert
the selected Word document" and its own workspace, searched Tora Projects,
and failed twice: nothing told it the file was C:\\Users\\torbe\\Documents\\
10 Mei 2026.docx. The name on the screen is not enough - Explorer hides
the extension and the folder is the window's, not the agent's.

Windows can say exactly, and quickly:
  * File Explorer: Shell.Application knows each window's folder and what is
    selected in it (one COM call, ~25 ms). A name traced over in the list
    ("10 Mei 2026") is matched to the file in that folder.
  * The desktop: the user's, OneDrive's and the public Desktop folders.
  * Word, Excel, PowerPoint: the open document's full path, from the app.
Never a guess: a name that matches no file, or two, gives nothing.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import threading
from typing import Optional

_log = logging.getLogger("tora.files")

_com = threading.local()
_OFFICE = {"WINWORD.EXE": ("Word.Application", "ActiveDocument"),
           "EXCEL.EXE": ("Excel.Application", "ActiveWorkbook"),
           "POWERPNT.EXE": ("PowerPoint.Application", "ActivePresentation")}


def _ready() -> None:
    if not getattr(_com, "done", False):
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:  # noqa: BLE001 - already, or no COM here
            pass
        _com.done = True


def root_window_at(x: float, y: float) -> int:
    """The top-level window under a physical point."""
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        return int(user32.GetAncestor(hwnd, 2) or 0) if hwnd else 0          # GA_ROOT
    except Exception:  # noqa: BLE001
        return 0


def foreground_window() -> int:
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        return 0


def _process_name(hwnd: int) -> str:
    try:
        from ctypes import wintypes
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)       # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(520)
            size = wintypes.DWORD(520)
            ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            return os.path.basename(buf.value).upper()
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return ""


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    try:
        ctypes.windll.user32.GetClassNameW(hwnd, buf, 128)
    except Exception:  # noqa: BLE001
        return ""
    return buf.value


def _explorer_view(hwnd: int):
    """The shell's view of the File Explorer window `hwnd`, or None."""
    if not hwnd:
        return None
    _ready()
    import comtypes.client
    windows = comtypes.client.CreateObject("Shell.Application", dynamic=True).Windows()
    for i in range(windows.Count):
        w = windows.Item(i)
        if w is not None and int(w.HWND) == int(hwnd):
            return w.Document
    return None


def explorer_window(hwnd: int) -> Optional[tuple[str, list[str]]]:
    """(folder, selected paths) of the File Explorer window `hwnd`, or None."""
    try:
        doc = _explorer_view(hwnd)
        if doc is None:
            return None
        selected = doc.SelectedItems()
        return str(doc.Folder.Self.Path), [str(selected.Item(j).Path) for j in range(selected.Count)]
    except Exception as e:  # noqa: BLE001 - not a folder window, or the shell would not say
        _log.debug("explorer lookup failed: %s", e)
    return None


def shown_items(hwnd: int) -> dict[str, list[str]]:
    """What the File Explorer window `hwnd` lists, by the name it shows
    (extension hidden or not, as Explorer shows it): {name: [paths]}. Where
    the folder is not one folder on disk - a library, Quick access, search
    results, Recent - this is the only way from a name to its file."""
    out: dict[str, list[str]] = {}
    try:
        doc = _explorer_view(hwnd)
        items = doc.Folder.Items() if doc is not None else None
        for i in range(items.Count if items is not None else 0):
            item = items.Item(i)
            path = str(item.Path or "")
            if path and os.path.exists(path):
                out.setdefault(str(item.Name or "").strip().lower(), []).append(path)
    except Exception as e:  # noqa: BLE001 - the shell would not say
        _log.debug("explorer items failed: %s", e)
    return out


def desktop_folders() -> list[str]:
    home = os.path.expanduser("~")
    out = [os.path.join(home, "Desktop"), os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop")]
    if os.environ.get("OneDrive"):
        out.insert(0, os.path.join(os.environ["OneDrive"], "Desktop"))
    return [d for d in out if os.path.isdir(d)]


def office_document(hwnd: int) -> Optional[str]:
    """The document open in Word, Excel or PowerPoint in front, as a path."""
    app = _OFFICE.get(_process_name(hwnd))
    if not app:
        return None
    _ready()
    try:
        import comtypes.client
        doc = getattr(comtypes.client.GetActiveObject(app[0], dynamic=True), app[1])
        path = str(doc.FullName or "")
        return path if os.path.isfile(path) else None
    except Exception as e:  # noqa: BLE001 - not saved yet, or the app is busy
        _log.debug("office lookup failed: %s", e)
        return None


def match(names: list[str], folders: list[str], prefer: list[str] = ()) -> list[str]:
    """Each name as shown (Explorer hides extensions) to the one file it is,
    in these folders. A name matching no file gives nothing; one matching
    two - "Report" for Report.docx and Report.pdf - gives the one of them
    that is selected (`prefer`), else nothing."""
    out: list[str] = []
    for name in names:
        name = (name or "").strip()
        if not name:
            continue
        files, dirs = [], []
        for folder in folders:
            try:
                entries = os.listdir(folder)
            except OSError:
                continue
            for e in entries:
                full = os.path.join(folder, e)
                if os.path.isfile(full) and (e.lower() == name.lower()
                                             or os.path.splitext(e)[0].lower() == name.lower()):
                    files.append(full)
                elif os.path.isdir(full) and e.lower() == name.lower():
                    dirs.append(full)
        # A file before a folder of the same name: 11 Oct, "turn this into a
        # PDF" over "YUSUF" went to the folder YUSUF.
        exact = [f for f in files if os.path.basename(f).lower() == name.lower()]
        hits = _one_of(exact or files or dirs, prefer)
        if hits and hits not in out:
            out.append(hits)
    return out


def _one_of(hits: list[str], prefer) -> Optional[str]:
    """The one hit - or, of several, the one selected. Never a guess."""
    if len(hits) == 1:
        return hits[0]
    chosen = [h for h in hits if os.path.normcase(h) in {os.path.normcase(p) for p in prefer}]
    return chosen[0] if len(chosen) == 1 else None


def files_at(names: list[str], hwnd: int) -> list[str]:
    """The files these names are, in the window `hwnd` - a File Explorer
    window's folder, or the desktop."""
    if not names:
        return []
    explorer = explorer_window(hwnd)
    if explorer is not None:
        # WR-01 (18 Oct 2026, the owner: "it isn't knowing what document I'm
        # talking about"): the folder on disk first; then what the window
        # lists by name, for a library, a search or Quick access, whose
        # "folder" is no folder; and when the trace placed no file at all,
        # what is selected in it.
        folder, selected = explorer
        found = match(names, [folder], prefer=selected) if os.path.isdir(folder) else []
        if len(found) < len(names):
            shown = shown_items(hwnd)
            for name in names:
                hit = _one_of(shown.get((name or "").strip().lower(), []), selected)
                if hit and hit not in found:
                    found.append(hit)
        return found or list(selected)
    if _class_name(hwnd) in ("Progman", "WorkerW"):
        return match(names, desktop_folders())
    return []


def in_view(hwnd: Optional[int] = None) -> list[str]:
    """No trace: what is selected in the File Explorer window in front, or
    the document open in the Office app in front."""
    hwnd = hwnd or foreground_window()
    explorer = explorer_window(hwnd)
    if explorer is not None:
        return explorer[1]
    doc = office_document(hwnd)
    return [doc] if doc else []


_CONVERT_RE = re.compile(r"\b(pdf|convert|export|save (it |this |them )?as|turn (it|this|them|these)\b.*\binto)\b",
                         re.IGNORECASE)


def is_conversion(goal: str) -> bool:
    """"Turn this into a PDF", "convert it to Word", "export as PDF"."""
    return bool(_CONVERT_RE.search(goal or ""))
