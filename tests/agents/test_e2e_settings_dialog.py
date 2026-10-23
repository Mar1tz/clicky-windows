"""Settings as a dialog with a menu (ui/settings_dialog.py, DESIGN.md §3D),
and the Data & privacy tab's controls over what Tora keeps."""
from __future__ import annotations

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp):
    from ui.app_window import ToraApp
    w = ToraApp()
    w.resize(1280, 820)
    yield w
    w.deleteLater()


def test_the_menu_is_the_agreed_tabs(window):
    from ui.settings_tabs import TABS
    assert [(sec, [k for k, *_r in rows]) for sec, rows in TABS] == [
        ("Settings", ["general", "system", "agents", "devlink", "advanced"]),
        ("Account", ["account", "usage", "plans", "privacy"])]
    window.open_settings()
    words = [r.text() for r in window._settings._rows.values()]
    assert words == ["General", "System", "Agents", "Code editors", "Advanced",
                     "Account", "Usage", "Plans & billing", "Data & privacy"]


def test_every_tab_builds(window):
    from ui.settings_tabs import TABS
    window.open_settings()
    for _sec, rows in TABS:
        for key, *_r in rows:
            window._settings.select(key)
            assert window._settings.current() == key
            assert window._settings._rows[key].isChecked()


def test_escape_and_a_click_outside_close_it(window, qapp):
    window.show()
    window.open_settings("system")
    dlg = window._settings
    QApplication.sendEvent(dlg, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                          Qt.KeyboardModifier.NoModifier))
    assert dlg.isHidden()
    window.open_settings()
    qapp.processEvents()
    outside = QPointF(5, 5)
    QApplication.sendEvent(dlg, QMouseEvent(QEvent.Type.MouseButtonPress, outside, outside,
                                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                            Qt.KeyboardModifier.NoModifier))
    assert dlg.isHidden(), "the dimmed app behind it closes it"
    window.hide()


# ── Data & privacy: what Tora keeps, and the controls over it ────────────────

def test_the_journal_exports_and_deletes(tmp_path):
    from tutor_features import journal
    journal.log_qa("What's wrong with this equation?", "Subtract the 6.", app_key="Excel")
    journal.log_walkthrough("Export the video", ["Click File.", "Click Export."])
    out = tmp_path / "history.json"
    assert journal.export_all(out) == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["kind"] == "journal" and {e["question"] for e in data["entries"]} >= {
        "What's wrong with this equation?"}
    assert journal.clear_all() == 2
    assert journal.recent(10) == [] and journal.walkthrough_seconds() == 0.0


def test_the_privacy_tab_says_where_the_journal_lives_and_links_the_policy(qapp):
    from ui.settings_tabs import PrivacyTab

    class Bridge:
        def __init__(self):
            self.done = []

        def switch(self, _name): return True
        def set_switch(self, *a): self.done.append(("switch",) + a)
        def grants(self): return []
        def open_journal_folder(self): self.done.append("folder")
        def open_web(self, path): self.done.append(("web", path))

    bridge = Bridge()
    page = PrivacyTab(bridge)
    texts = [l.text() for l in page.findChildren(QLabel)]
    assert "Stored on this PC only" in texts and "Delete your history" in texts
    from PyQt6.QtWidgets import QPushButton
    buttons = {b.text(): b for b in page.findChildren(QPushButton)}
    buttons["Privacy policy"].click()
    buttons["Terms"].click()
    buttons["Open folder"].click()
    assert bridge.done == [("web", "/privacy"), ("web", "/terms"), "folder"]


def test_plans_match_the_website_and_mark_yours(qapp):
    from PyQt6.QtWidgets import QPushButton
    from ui.settings_tabs import PLANS, PlansTab

    class Bridge:
        def __init__(self):
            self.done = []

        def account_state(self):
            return {"signed_in": True, "mode": "account", "summary": {"plan": "pro"}}

        def open_web(self, path):
            self.done.append(path)

    assert [(p["name"], p["price"]) for p in PLANS] == [("Free", "$0"), ("Pro", "$20"), ("Max", "$100")]
    bridge = Bridge()
    page = PlansTab(bridge)
    buttons = {b.text(): b for b in page.findChildren(QPushButton)}
    assert not buttons["Your plan"].isEnabled(), "yours is marked, not offered"
    assert "Choose Pro" not in buttons and {"Choose Free", "Choose Max"} <= set(buttons)
    # What choosing does - Paddle's checkout - is tests/agents/test_e2e_billing.py.


def test_pro_and_max_can_buy_credits_and_free_cannot(qapp):
    """Credits (0012): the balance and the pack, on paid plans only."""
    from PyQt6.QtWidgets import QPushButton
    from ui.settings_tabs import PlansTab

    def tab(plan, can_buy):
        class Bridge:
            def account_state(self):
                return {"signed_in": True, "mode": "account", "summary": {
                    "plan": plan, "billing": plan != "free",
                    "credits": {"month": 900, "month_used": 300, "month_left": 600, "bought_left": 675,
                                "can_buy": can_buy},
                    "credit_packs": [{"price": "pri_675", "credits": 675, "label": "$10"},
                                     {"price": "pri_1750", "credits": 1750, "label": "$25"}]
                                    if can_buy else []}}
        page = PlansTab(Bridge())
        return ({b.text() for b in page.findChildren(QPushButton)},
                [label.text() for label in page.findChildren(QLabel)])

    buttons, texts = tab("pro", True)
    assert {"675 credits - $10", "1,750 credits - $25"} <= buttons, "every pack Paddle has"
    assert any(t.startswith("600 of 900 credits left this month · 675 added") for t in texts)
    buttons, _texts = tab("free", False)
    assert not any("credits -" in b for b in buttons), "Free upgrades instead"


def test_the_agents_panels_lose_their_dark_sheet(qapp):
    """Drawn for the old dark windows; in Settings they take the app's look."""
    from PyQt6.QtWidgets import QWidget
    from ui.app_pages import _tidy
    w = QWidget()
    w.setStyleSheet("QLineEdit { background: rgb(40, 40, 50); }")
    w.setObjectName("panel")
    _tidy(w)
    assert w.styleSheet() == "" and w.objectName() == "embedded"



# ── General: the shortcut and the microphone list ─────────────────────────────

@pytest.mark.parametrize("combo, ok", [
    ("ctrl+win", True), ("ctrl+alt+space", True), ("alt+q", True),
    ("ctrl", False), ("shift+a", False), ("ctrl+c", False), ("ctrl+a+b", False)])
def test_a_shortcut_must_be_one_you_cannot_type_by_accident(combo, ok):
    from ui.app_bridge import check_shortcut
    assert (check_shortcut(combo) == "") is ok, combo


def test_a_new_shortcut_goes_live_through_the_running_app(monkeypatch):
    from ui import app_bridge
    seen = []
    monkeypatch.setitem(app_bridge.HOTKEY_HOOKS, "rebind", lambda combo: seen.append(combo) or "")
    assert app_bridge.AppBridge().set_hotkey("ctrl+alt+space") == ""
    assert seen == ["ctrl+alt+space"]
    assert app_bridge.AppBridge().set_hotkey("shift+a") != "" and seen == ["ctrl+alt+space"], \
        "a bad one is refused before it reaches the hotkey"


def test_the_microphone_list_is_each_mic_once(monkeypatch):
    """Windows lists one headset once per audio system, beside its routing
    entries; the list keeps the default's audio system, each name once."""
    import sounddevice as sd
    from ui.app_bridge import AppBridge
    devices = [{"name": "Microsoft Sound Mapper - Input", "hostapi": 0, "max_input_channels": 2},
               {"name": "Microphone (Realtek(R) Audio)", "hostapi": 0, "max_input_channels": 2},
               {"name": "Headset (Jabra)", "hostapi": 0, "max_input_channels": 1},
               {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
               {"name": "Microphone (Realtek(R) Audio)", "hostapi": 1, "max_input_channels": 2},
               {"name": "Input ()", "hostapi": 3, "max_input_channels": 2}]
    monkeypatch.setattr(sd, "query_devices", lambda: devices)
    monkeypatch.setattr(sd.default, "device", (1, 3))
    assert AppBridge().mics() == [(1, "Microphone (Realtek(R) Audio)", True), (2, "Headset (Jabra)", False)]



# ── Account: deleting it ──────────────────────────────────────────────────────

def test_delete_account_needs_the_email_typed_and_goes_back_to_sign_in(qapp):
    """Delete stays off until the email is typed; the server's no is shown
    in the dialog; a yes closes it and takes the app back to sign-in."""
    import time
    from PyQt6.QtWidgets import QWidget
    from ui.settings_parts import DeleteAccountDialog

    class Bridge:
        answer, asked = "Cancel your plan on the HeyTora website first.", []

        def delete_account(self, email):
            self.asked.append(email)
            return self.answer

    br, gone, host = Bridge(), [], QWidget()
    host.resize(800, 600)
    dlg = DeleteAccountDialog(host, br, "amy@example.com", lambda: gone.append(1))
    dlg.open()

    def wait(cond):
        end = time.time() + 5
        while not cond() and time.time() < end:
            qapp.processEvents()
        assert cond()

    assert not dlg.delete.isEnabled()
    dlg.typed.setText("amy@example.co")
    assert not dlg.delete.isEnabled()
    dlg.typed.setText(" AMY@example.com")
    assert dlg.delete.isEnabled()
    dlg.delete.click()
    wait(lambda: not dlg._busy)
    assert br.asked == ["AMY@example.com"] and not gone
    assert "Cancel your plan" in dlg.problem.text() and dlg.delete.isEnabled()
    br.answer = ""
    dlg.delete.click()
    wait(lambda: gone == [1])
    assert dlg.isHidden()



# ── Confirmations: the app's own card, never Windows' message box ─────────────

def test_confirm_is_the_apps_card_and_only_yes_is_yes(qapp):
    """Seen 6 Oct: signing out asked in a grey Windows box with Yes/Cancel.
    Now Wispr's card: a title, one line, Back to account and a red Sign out."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QPushButton, QWidget
    from ui.settings_parts import SubDialog, confirm
    host = QWidget()
    host.resize(800, 600)

    def answer(text):
        def press():
            card = host.findChildren(SubDialog)[-1]
            next(b for b in card.findChildren(QPushButton) if b.text() == text).click()
        QTimer.singleShot(30, press)

    answer("Sign out")
    assert confirm(host, "Are you sure you want to sign out?", "Body", "Sign out", "Back to account")
    answer("Back to account")
    assert not confirm(host, "Are you sure you want to sign out?", "Body", "Sign out", "Back to account")
    QTimer.singleShot(30, lambda: host.findChildren(SubDialog)[-1].close_dialog())
    assert not confirm(host, "Sure?", "Body", "Yes"), "closed is no"


def test_no_settings_confirmation_uses_windows_message_box():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for name in ("ui/settings_tabs.py", "ui/app_pages.py"):
        assert "QMessageBox" not in (root / name).read_text(encoding="utf-8"), name
