"""HeyTora keeps itself up to date - no download page, no setup screens.

Releases are published by tools/release.py: the installer on GitHub
Releases, and a small manifest beside the download page (MANIFEST_URL):

    {"version": "1.6.5", "url": ".../Setup-HeyTora.exe", "sha256": "...",
     "size": 200978416, "notes": "What changed", "min_version": "1.6.0"}

The installed app reads it at start-up and every CHECK_EVERY_S. A newer
version is downloaded in the background into the data folder and only
trusted if its size and SHA-256 match the manifest (served over HTTPS, as
is the file). Then it is "ready", and it is installed over the existing
app - silently, by the same Inno Setup installer with /VERYSILENT, which
keeps the folder, the shortcuts and the start-with-Windows choice:

  * now, when the user says so ("Restart to update": tray or Settings) -
    HeyTora closes, the installer runs, HeyTora opens again;
  * or by itself (Settings → "Install updates automatically", on by
    default): the next time HeyTora starts - before anything is shown - or
    as it is quit;
  * at once, when the manifest's min_version is above this version: an
    update that is required (an old app the server no longer serves).

An installer that did not take (still the old version after it ran) is
tried MAX_ATTEMPTS times, then left to the user, with the download page.

Run from source there is nothing to install over: updates are off ("dev").
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from version import __version__

_log = logging.getLogger("tora.updates")

MANIFEST_URL = "https://qwickly.github.io/heytora/latest.json"
DOWNLOAD_PAGE = "https://qwickly.github.io/heytora/"
CHECK_EVERY_S = 6 * 3600
MAX_ATTEMPTS = 2
INSTALLER_FLAGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/UPDATE=1"]


def parse_version(text: str) -> tuple[int, ...]:
    """"1.6.10" -> (1, 6, 10). Anything that is not a version is (0,)."""
    try:
        return tuple(int(part) for part in str(text).strip().lstrip("v").split("."))
    except ValueError:
        return (0,)


def newer(a: str, b: str) -> bool:
    """Is version `a` newer than `b`?"""
    return parse_version(a) > parse_version(b)


@dataclass
class Release:
    version: str
    url: str
    sha256: str
    size: int = 0
    notes: str = ""
    min_version: str = ""

    @classmethod
    def from_manifest(cls, data: dict) -> "Release":
        rel = cls(version=str(data.get("version") or ""), url=str(data.get("url") or ""),
                  sha256=str(data.get("sha256") or "").lower(), size=int(data.get("size") or 0),
                  notes=str(data.get("notes") or ""), min_version=str(data.get("min_version") or ""))
        if parse_version(rel.version) == (0,) or not rel.url.startswith("https://") \
                or len(rel.sha256) != 64:
            raise ValueError("not a HeyTora release manifest")
        return rel


def _data_folder() -> Path:
    from config import data_dir
    return data_dir() / "updates"


def _spawn(argv: list[str]) -> None:
    """Start the installer on its own: it outlives this app, which it replaces."""
    flags = 0
    if sys.platform == "win32":
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    try:
        subprocess.Popen(argv, creationflags=flags, close_fds=True)
    except OSError:
        # A job that does not allow breaking away (some shells): without it.
        subprocess.Popen(argv, creationflags=flags & ~getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
                         close_fds=True)


class Updater:
    """Checks, downloads and installs updates. Thread-safe; listeners are
    called on any thread (the UI hops onto Qt's)."""

    def __init__(self, current: str = __version__, *, folder: Optional[Path] = None,
                 manifest_url: str = "", transport: Optional[httpx.BaseTransport] = None,
                 installed: Optional[bool] = None, spawn: Callable[[list[str]], None] = _spawn):
        self.current = current
        self._folder = folder
        self._url = manifest_url or os.environ.get("TORA_UPDATE_URL", "").strip() or MANIFEST_URL
        self._transport = transport
        # Only an installed copy can be updated in place; from source, no.
        self.installed = bool(getattr(sys, "frozen", False)) if installed is None else installed
        self._spawn = spawn
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self._state = "idle" if self.installed else "dev"
        self._release: Optional[Release] = None
        self._progress = 0.0
        self._error = ""
        self._checked_at = 0.0
        self._busy = False
        self._just_updated = ""

    # -- what the screens show ------------------------------------------------------------------------

    def state(self) -> dict:
        """{state, current, latest, notes, progress, error, required, checked_at}.
        state: dev | idle | checking | current | downloading | ready | installing | failed."""
        with self._lock:
            rel = self._release
            return {"state": self._state, "current": self.current,
                    "latest": rel.version if rel else "", "notes": rel.notes if rel else "",
                    "progress": self._progress, "error": self._error,
                    "required": self._required(rel), "checked_at": self._checked_at,
                    "just_updated": self._just_updated, "page": DOWNLOAD_PAGE}

    def on_change(self, listener: Callable[[], None]) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def _set(self, **fields) -> None:
        with self._lock:
            for name, value in fields.items():
                setattr(self, f"_{name}", value)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener()
            except Exception:  # noqa: BLE001 - a screen must never stop an update
                _log.debug("update listener failed", exc_info=True)

    def _required(self, rel: Optional[Release]) -> bool:
        return bool(rel and rel.min_version and newer(rel.min_version, self.current))

    # -- the folder ----------------------------------------------------------------------------------------

    def folder(self) -> Path:
        folder = self._folder or _data_folder()
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _pending_path(self) -> Path:
        return self.folder() / "pending.json"

    def _pending(self) -> dict:
        try:
            return json.loads(self._pending_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_pending(self, data: dict) -> None:
        if data:
            self._pending_path().write_text(json.dumps(data), encoding="utf-8")
        else:
            try:
                self._pending_path().unlink()
            except FileNotFoundError:
                pass

    # -- checking and downloading ------------------------------------------------------------------------

    def check_soon(self, force: bool = False) -> bool:
        """Check (and download) in the background, unless it was done in the
        last CHECK_EVERY_S. True if a check was started."""
        with self._lock:
            if not self.installed or self._busy or self._state in ("installing",):
                return False
            if not force and self._checked_at and time.time() - self._checked_at < CHECK_EVERY_S:
                return False
            self._busy = True
        threading.Thread(target=self._check, name="heytora-update", daemon=True).start()
        return True

    def _http(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), follow_redirects=True,
                            transport=self._transport,
                            headers={"User-Agent": f"HeyTora/{self.current} (updater)"})

    def _check(self) -> None:
        try:
            self._set(state="checking", error="")
            with self._http(15.0) as http:
                r = http.get(self._url, params={"t": int(time.time() // 300)})   # past any stale cache
                r.raise_for_status()
                rel = Release.from_manifest(r.json())
            with self._lock:
                self._release, self._checked_at = rel, time.time()
            if not newer(rel.version, self.current):
                _log.info("up to date (%s; published %s)", self.current, rel.version)
                self._set(state="current")
                return
            _log.info("update available: %s -> %s%s", self.current, rel.version,
                      " (required)" if self._required(rel) else "")
            path = self._download(rel)
            pending = self._pending()
            attempts = pending.get("attempts", 0) if pending.get("version") == rel.version else 0
            self._write_pending({"version": rel.version, "path": str(path), "sha256": rel.sha256,
                                 "attempts": attempts, "notes": rel.notes})
            if attempts >= MAX_ATTEMPTS:
                self._set(state="failed", error="The update didn't install - download it from the website.")
                return
            self._set(state="ready", progress=1.0)
        except Exception as e:  # noqa: BLE001 - offline, the site down: say so, try later
            _log.info("update check failed: %s", e)
            with self._lock:
                self._checked_at = time.time() - CHECK_EVERY_S + 30 * 60   # again in half an hour
            self._set(state="failed" if self._release and newer(self._release.version, self.current)
                      else "idle", error=f"Couldn't check for updates: {e}")
        finally:
            with self._lock:
                self._busy = False

    def _download(self, rel: Release) -> Path:
        """The installer, verified - or an exception. Reused if already here."""
        target = self.folder() / f"Setup-HeyTora-{rel.version}.exe"
        if target.exists() and _sha256(target) == rel.sha256:
            return target
        part = target.with_suffix(".part")
        self._set(state="downloading", progress=0.0)
        digest = hashlib.sha256()
        done = 0
        with self._http(60.0) as http, http.stream("GET", rel.url) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or rel.size or 0)
            with open(part, "wb") as out:
                for chunk in r.iter_bytes(1 << 16):
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if total:
                        self._progress = min(0.99, done / total)
            self._set(progress=self._progress)
        if digest.hexdigest() != rel.sha256 or (rel.size and done != rel.size):
            part.unlink(missing_ok=True)
            raise ValueError("the download didn't match the published installer - not used")
        os.replace(part, target)
        # Older installers are of no use now.
        for old in self.folder().glob("Setup-HeyTora-*.exe"):
            if old != target:
                old.unlink(missing_ok=True)
        _log.info("update %s downloaded and verified", rel.version)
        return target

    # -- installing --------------------------------------------------------------------------------------

    def install_now(self, relaunch: bool = True) -> bool:
        """Run the ready installer over this app, silently. The caller must
        then close HeyTora at once - the installer waits for it. False if
        there is nothing (verified) to install."""
        pending = self._pending()
        path = Path(pending.get("path") or "")
        if not self.installed or not pending or not path.exists() \
                or not newer(pending.get("version", ""), self.current) \
                or _sha256(path) != pending.get("sha256"):
            return False
        pending["attempts"] = int(pending.get("attempts", 0)) + 1
        self._write_pending(pending)
        log = self.folder() / f"install-{pending['version']}.log"
        argv = [str(path), *INSTALLER_FLAGS, f"/RELAUNCH={1 if relaunch else 0}", f"/LOG={log}"]
        _log.info("installing update %s (attempt %d)%s", pending["version"], pending["attempts"],
                  "" if relaunch else " as HeyTora closes")
        self._set(state="installing")
        self._spawn(argv)
        return True

    def at_startup(self, auto: bool) -> str:
        """Before anything is shown. Returns
             "installing"  an update is being installed - close HeyTora now
             "updated"     this run is the update that was installed
             ""            carry on
        """
        if not self.installed:
            return ""
        pending = self._pending()
        if not pending:
            return ""
        if not newer(pending.get("version", ""), self.current):
            # It took: this is the new version (or a newer one).
            _log.info("updated to %s", self.current)
            with self._lock:
                self._just_updated = self.current
            self._write_pending({})
            for leftover in self.folder().glob("Setup-HeyTora-*.exe"):
                leftover.unlink(missing_ok=True)
            return "updated"
        if int(pending.get("attempts", 0)) >= MAX_ATTEMPTS:
            self._set(state="failed", error="The update didn't install - download it from the website.")
            return ""
        if auto and self.install_now(relaunch=True):
            return "installing"
        return ""

    def ready(self) -> bool:
        with self._lock:
            return self._state == "ready"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


_updater: Optional[Updater] = None
_updater_lock = threading.Lock()


def updater() -> Updater:
    """The one Updater for the app."""
    global _updater
    with _updater_lock:
        if _updater is None:
            _updater = Updater()
        return _updater


def reset(new: Optional[Updater] = None) -> None:
    """Replace the app's Updater (tests)."""
    global _updater
    with _updater_lock:
        _updater = new
