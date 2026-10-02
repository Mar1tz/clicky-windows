"""HeyTora keeps itself up to date (2 Oct 2026).

"When I push new versions I don't want them to go download new things -
it just does it to the existing app." The installed app reads a manifest
beside the download page, downloads a newer installer in the background,
trusts it only if size and SHA-256 match, and installs it over itself with
/VERYSILENT - when the user says "Restart to update", or by itself the next
time it starts or as it is quit; at once when the version is required.
tools/release.py publishes all of it in one go. No network here: a fake
site, a fake installer.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx  # noqa: E402
import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = b"MZ" + b"\x00" * 4096 + b"pretend installer"
SHA = hashlib.sha256(INSTALLER).hexdigest()
URL = "https://github.com/Qwickly/heytora/releases/download/v1.7.0/Setup-HeyTora.exe"


class FakeSite:
    def __init__(self, version="1.7.0", body=INSTALLER, sha=SHA, min_version=""):
        self.manifest = {"version": version, "url": URL, "sha256": sha, "size": len(body),
                         "notes": "Plans that move with use.", "min_version": min_version}
        self.body = body
        self.offline = False
        self.downloads = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("no internet")
        if request.url.path.endswith("latest.json"):
            return httpx.Response(200, json=self.manifest)
        if request.url.path.endswith("Setup-HeyTora.exe"):
            self.downloads += 1
            return httpx.Response(200, content=self.body, headers={"content-length": str(len(self.body))})
        return httpx.Response(404)


def _updater(tmp_path, site, current="1.6.4", installed=True):
    from updates.service import Updater
    spawned = []
    u = Updater(current, folder=tmp_path / "updates", manifest_url="https://qwickly.github.io/heytora/latest.json",
                transport=httpx.MockTransport(site), installed=installed, spawn=spawned.append)
    return u, spawned


def _check(u):
    assert u.check_soon(force=True)
    end = time.monotonic() + 10
    while u.state()["state"] in ("idle", "checking", "downloading") and time.monotonic() < end:
        time.sleep(0.02)
    end = time.monotonic() + 2
    while u._busy and time.monotonic() < end:
        time.sleep(0.01)
    return u.state()


# -- versions and the manifest ------------------------------------------------------------------------------------

def test_versions_compare_as_numbers():
    from updates import newer, parse_version
    assert parse_version("1.6.10") == (1, 6, 10) and parse_version("v2.0") == (2, 0)
    assert newer("1.6.10", "1.6.9") and not newer("1.6.4", "1.6.4") and not newer("junk", "1.0")


def test_a_manifest_that_is_not_one_is_refused():
    from updates import Release
    for bad in ({}, {"version": "1.7.0", "url": "http://x/Setup.exe", "sha256": SHA},
                {"version": "1.7.0", "url": URL, "sha256": "short"}):
        with pytest.raises(ValueError):
            Release.from_manifest(bad)


# -- checking and downloading -------------------------------------------------------------------------------------

def test_a_newer_version_is_downloaded_verified_and_ready(tmp_path):
    site = FakeSite()
    u, spawned = _updater(tmp_path, site)
    st = _check(u)
    assert st["state"] == "ready" and st["latest"] == "1.7.0" and st["notes"] == "Plans that move with use."
    pending = json.loads((tmp_path / "updates" / "pending.json").read_text())
    assert pending["version"] == "1.7.0" and Path(pending["path"]).read_bytes() == INSTALLER
    assert spawned == [], "nothing installs until it is asked for"


def test_a_download_that_does_not_match_is_never_used(tmp_path):
    site = FakeSite(body=INSTALLER + b"tampered")          # the manifest's hash is the real one
    u, _spawned = _updater(tmp_path, site)
    st = _check(u)
    assert st["state"] != "ready" and "didn't match" in st["error"]
    assert not list((tmp_path / "updates").glob("Setup-HeyTora-*")), "nothing kept"
    assert u.install_now() is False


def test_up_to_date_says_so(tmp_path):
    u, _ = _updater(tmp_path, FakeSite(version="1.6.4"))
    assert _check(u)["state"] == "current"


def test_offline_tries_again_later_without_a_fuss(tmp_path):
    site = FakeSite()
    site.offline = True
    u, _ = _updater(tmp_path, site)
    st = _check(u)
    assert st["state"] == "idle" and "Couldn't check" in st["error"]
    assert u.check_soon() is False, "not again at once"


def test_a_required_version_is_flagged(tmp_path):
    u, _ = _updater(tmp_path, FakeSite(version="1.7.0", min_version="1.7.0"))
    assert _check(u)["required"] is True
    u2, _ = _updater(tmp_path / "b", FakeSite(version="1.7.0", min_version="1.6.0"))
    assert _check(u2)["required"] is False


def test_from_source_there_is_nothing_to_update(tmp_path):
    u, spawned = _updater(tmp_path, FakeSite(), installed=False)
    assert u.state()["state"] == "dev" and u.check_soon(force=True) is False
    assert u.at_startup(auto=True) == "" and spawned == []


# -- installing over the existing app ------------------------------------------------------------------------------

def test_restart_to_update_runs_the_installer_silently_over_the_app(tmp_path):
    u, spawned = _updater(tmp_path, FakeSite())
    _check(u)
    assert u.install_now(relaunch=True)
    argv = spawned[0]
    assert argv[0].endswith("Setup-HeyTora-1.7.0.exe")
    for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/UPDATE=1",
                 "/RELAUNCH=1"):
        assert flag in argv, flag
    assert any(a.startswith("/LOG=") for a in argv)
    assert u.state()["state"] == "installing"


def test_quitting_installs_without_reopening(tmp_path):
    u, spawned = _updater(tmp_path, FakeSite())
    _check(u)
    assert u.install_now(relaunch=False) and "/RELAUNCH=0" in spawned[0]


def test_a_ready_update_installs_by_itself_at_the_next_start(tmp_path):
    u, _ = _updater(tmp_path, FakeSite())
    _check(u)
    later, spawned = _updater(tmp_path, FakeSite())          # HeyTora started again, same version
    assert later.at_startup(auto=True) == "installing" and spawned
    off, spawned_off = _updater(tmp_path, FakeSite())
    assert off.at_startup(auto=False) == "" and spawned_off == [], "only if updates install by themselves"


def test_the_new_version_says_it_was_updated_and_tidies_up(tmp_path):
    u, _ = _updater(tmp_path, FakeSite())
    _check(u)
    new, spawned = _updater(tmp_path, FakeSite(), current="1.7.0")   # the installer did its job
    assert new.at_startup(auto=True) == "updated" and spawned == []
    assert new.state()["just_updated"] == "1.7.0"
    assert not (tmp_path / "updates" / "pending.json").exists()
    assert not list((tmp_path / "updates").glob("Setup-HeyTora-*"))


def test_an_installer_that_does_not_take_is_not_tried_forever(tmp_path):
    from updates.service import MAX_ATTEMPTS
    u, _ = _updater(tmp_path, FakeSite())
    _check(u)
    for _ in range(MAX_ATTEMPTS):                            # it ran, and HeyTora is still 1.6.4
        again, spawned = _updater(tmp_path, FakeSite())
        assert again.at_startup(auto=True) == "installing"
    last, spawned = _updater(tmp_path, FakeSite())
    assert last.at_startup(auto=True) == "" and spawned == []
    assert last.state()["state"] == "failed" and "website" in last.state()["error"]


# -- the screens ---------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _Bridge:
    def __init__(self, state):
        from ui.app_bridge import AppBridge
        self._real = AppBridge()
        self.state_ = state
        self.done = []

    def update_state(self):
        return dict(self.state_)

    def install_update(self):
        self.done.append("install")

    def check_updates(self):
        self.done.append("check")

    def set_auto_update(self, on):
        self.done.append(("auto", on))

    def open_download_page(self):
        self.done.append("page")

    def __getattr__(self, name):
        return getattr(self._real, name)


def _card(qapp, **state):
    from PyQt6.QtWidgets import QLabel, QPushButton
    from ui.app_pages import SettingsPage
    base = {"state": "current", "current": "1.6.4", "latest": "1.6.4", "notes": "", "progress": 0.0,
            "error": "", "required": False, "auto": True}
    bridge = _Bridge(base | state)
    page = SettingsPage(bridge)
    card = page._updates_card()
    _keep.append((page, card))                 # alive until the test has clicked
    texts = [w.text() for w in card.findChildren(QLabel)]
    buttons = {b.text(): b for b in card.findChildren(QPushButton)}
    return bridge, texts, buttons


_keep: list = []


def test_settings_offers_restart_to_update_when_ready(qapp):
    bridge, texts, buttons = _card(qapp, state="ready", latest="1.7.0", notes="Plans that move with use.")
    assert "HeyTora 1.7.0 is ready" in texts and "What's new: Plans that move with use." in texts
    buttons["Restart to update"].click()
    assert bridge.done == ["install"]


def test_settings_shows_the_download_as_it_happens(qapp):
    _bridge, texts, _buttons = _card(qapp, state="downloading", latest="1.7.0", progress=0.42)
    assert "Downloading HeyTora 1.7.0…" in texts and "You have 1.6.4 · 42%" in texts


def test_settings_up_to_date_and_check_now(qapp):
    bridge, texts, buttons = _card(qapp)
    assert "Up to date." in texts
    buttons["Check now"].click()
    assert bridge.done == ["check"]


def test_settings_a_failed_update_points_to_the_download_page(qapp):
    bridge, texts, buttons = _card(qapp, state="failed", error="The update didn't install - download it from the website.")
    buttons["Download page"].click()
    assert bridge.done == ["page"]


def test_settings_from_source(qapp):
    _bridge, texts, buttons = _card(qapp, state="dev")
    assert any("Running from source" in t for t in texts) and "Restart to update" not in buttons


def test_the_tray_offers_the_update(qapp, tmp_path, monkeypatch):
    from updates import service
    from ui.tray import TrayManager
    u, _ = _updater(tmp_path, FakeSite())
    _check(u)
    monkeypatch.setattr(service, "_updater", u)
    tray = TrayManager()
    try:
        tray._refresh_updates()
        assert tray._update_item == "ready:1.7.0"
        texts = [a.text() for a in tray._tray.contextMenu().actions()]
        assert any("Restart to update to HeyTora 1.7.0" in t for t in texts)
    finally:
        tray.hide_icon()


# -- the installer, the release tool, the start-up -----------------------------------------------------------------

def test_the_installer_reopens_heytora_after_a_silent_update():
    iss = (ROOT / "installer.iss").read_text(encoding="utf-8")
    assert 'Flags: nowait skipifnotsilent; Check: ShouldRelaunch' in iss
    assert "{param:RELAUNCH|1}" in iss and "{param:UPDATE|0}" in iss and "Sleep(2500)" in iss
    assert "CloseApplications=yes" in iss
    # An update leaves "start with Windows" as the user left it in Settings
    # (the first install's task would switch it back on - found live, 2 Oct).
    assert "Tasks: startupicon; Check: NotUpdate" in iss


def test_the_release_tool_changes_only_the_version(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("release", ROOT / "tools" / "release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    for name in ("version.py", "installer.iss"):
        (tmp_path / name).write_bytes((ROOT / name).read_bytes())
    monkeypatch.setattr(release, "ROOT", tmp_path)
    before = {n: (tmp_path / n).read_bytes() for n in ("version.py", "installer.iss")}
    release.set_version("9.9.9")
    for name, old in before.items():
        new = (tmp_path / name).read_bytes()
        assert b"9.9.9" in new and len(new.splitlines()) == len(old.splitlines())
        changed = [a for a, b in zip(old.splitlines(), new.splitlines()) if a != b]
        assert len(changed) == 1, f"{name}: only the version line"
    assert release.current_version() == "9.9.9"


def test_the_app_knows_its_version():
    from version import __version__
    iss = (ROOT / "installer.iss").read_text(encoding="utf-8")
    assert f'#define MyAppVersion     "{__version__}"' in iss, "version.py and the installer agree"


def test_an_update_installs_before_anything_is_shown():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    at_start = src.index("update_outcome = upd.at_startup(")
    assert at_start < src.index("door = sign_in_gate.gate()") < src.index("manager = CompanionManager()")
    assert 'if update_outcome == "installing":\n        return' in src
