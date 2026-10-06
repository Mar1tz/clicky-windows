"""The Tora app window's one door into the running companion (APP-10).

The window was a drawing: nine pages of placeholder numbers and switches
that did nothing, on purpose (M29 - "place everything where it needs to go,
no functionality yet"). This is what makes it real, and it is thin by
design:

  * READS come from where the running app already keeps things - the
    manager, config, the journal, the agent workforce. Nothing is copied,
    so nothing can go stale.
  * CHANGES go down the SAME path the tray menu uses: a switch here calls
    TrayManager.apply_switch, which ticks the tray's own menu and emits the
    tray's signal, which main.py connects to the manager. The tray and the
    app can never disagree, and main.py stays the one place that says which
    control drives which handler.

Every method copes with pieces missing - no tray, no workforce, no manager -
because the window must open even when half of Tora did not start.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PyQt6.QtCore import QObject, pyqtSignal

_log = logging.getLogger("tora.app")


# Set by main.py once the global hotkey is up: change it live (rebind ->
# "" or why not), and hold it quiet while a new one is being pressed
# (claim/release). Empty when the window runs on its own: a change is saved
# for the next start instead.
HOTKEY_HOOKS: dict = {}

MODIFIERS = ("ctrl", "alt", "shift", "win")


def check_shortcut(combo: str) -> str:
    """Why a push-to-talk combo will not do, or "" when it will. It must
    hold Ctrl, Alt or Win (Shift alone is typing), and be two keys or more:
    either modifiers only ("ctrl+win") or modifiers then one key."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if len(parts) < 2:
        return "Use two keys or more - Ctrl + Win, or Ctrl + Alt + Space."
    if not any(p in ("ctrl", "alt", "win") for p in parts):
        return "Include Ctrl, Alt or Win - Shift and a key is just typing."
    keys = [p for p in parts if p not in MODIFIERS]
    if len(keys) > 1:
        return "Hold modifiers and at most one other key."
    if keys and parts[-1] != keys[0]:
        return "Put the ordinary key last."
    if keys and keys[0] in ("c", "v", "x", "z", "a", "s", "tab", "esc", "escape", "enter", "return"):
        return f"{' + '.join(p.capitalize() for p in parts)} is taken by Windows and most apps."
    return ""


class AppBridge(QObject):
    """What the app window can read and do. Nothing else."""

    # Something the pages show has changed: "state", "account", "agents",
    # "guide", "switches", "journal", "recording", "skills". A page shown at
    # the time refreshes itself.
    changed = pyqtSignal(str)

    def __init__(self, manager: Any = None, tray: Any = None, panel: Any = None):
        super().__init__()
        self.manager, self.tray, self.panel = manager, tray, panel
        self._state = "idle"
        if manager is not None:
            manager.sig_state_changed.connect(self._on_state)
            for signal, topic in (("sig_account_problem", "account"),
                                  ("sig_agents_ready", "agents"),
                                  ("sig_guide_progress", "guide"),
                                  ("sig_recording_state", "recording")):
                sig = getattr(type(manager), signal, None)
                if sig is not None:
                    getattr(manager, signal).connect(
                        lambda *_a, _t=topic: self.changed.emit(_t))
            if getattr(type(manager), "sig_agent_event", None) is not None:
                manager.sig_agent_event.connect(self._on_agent_event)

    def _on_state(self, state) -> None:
        was = self._state
        self._state = str(getattr(state, "name", state) or "idle").lower()
        self.changed.emit("state")
        # A turn has just ended, and what was asked is in the journal now.
        if self._state == "idle" and was != "idle":
            self.changed.emit("journal")

    def _on_agent_event(self, ev) -> None:
        self.changed.emit("agents")

    # ── what is happening ────────────────────────────────────────────────────

    def state(self) -> str:
        """idle / listening / thinking / speaking."""
        return self._state

    def live(self) -> bool:
        """Is the live conversation open right now?"""
        conv = getattr(self.manager, "_realtime", None)
        try:
            return bool(conv is not None and conv.ready)
        except Exception:  # noqa: BLE001
            return False

    def problem(self) -> str:
        """"billing" / "key" while Google is refusing the account (APP-02)."""
        return str(getattr(self.manager, "_problem_kind", "") or "")

    def listening(self) -> str:
        """"ambient" - wakes on "Tora" - or "hotkey"."""
        from config import cfg
        return "ambient" if cfg.ambient_mic() else "hotkey"

    def hotkey(self) -> str:
        from config import cfg
        return " + ".join(p.strip().capitalize() for p in (cfg.hotkey or "").split("+") if p.strip())

    def hotkey_keys(self) -> list[str]:
        """The shortcut as keys: ["Ctrl", "Win"]."""
        from config import cfg
        return [p.strip().capitalize() for p in (cfg.hotkey or "").split("+") if p.strip()]

    def set_hotkey(self, combo: str) -> str:
        """A new push-to-talk shortcut - live when the app is running, else
        from the next start. "" when it worked, otherwise why not."""
        why = check_shortcut(combo)
        if why:
            return why
        rebind = HOTKEY_HOOKS.get("rebind")
        if rebind is not None:
            why = rebind(combo)
        else:
            from config import cfg
            cfg.set_hotkey(combo)
        if not why:
            self.changed.emit("switches")
        return why

    def hold_hotkey(self, on: bool) -> None:
        """While a new shortcut is being pressed, the old one does nothing."""
        hook = HOTKEY_HOOKS.get("claim" if on else "release")
        if hook is not None:
            hook()

    def mic_name(self) -> str:
        """The input device Tora is listening on, by name."""
        from config import cfg
        try:
            import sounddevice as sd
            index = cfg.mic_device()
            info = sd.query_devices(index if index is not None else None, kind="input")
            return str(info.get("name", "")).strip()
        except Exception:  # noqa: BLE001 - no audio stack is not an error here
            return ""

    def walkthrough(self) -> Optional[dict]:
        """The walkthrough running now: goal, step, total - or None."""
        m = self.manager
        runner = getattr(m, "_guide_runner", None)
        plan = getattr(m, "_lesson_plan", None)
        if runner is None or plan is None or not getattr(runner, "running", False):
            return None
        writing = plan is getattr(m, "_plan_in_progress", None)
        return {"goal": getattr(plan, "goal", "") or "",
                "step": max(0, getattr(runner, "index", 0)) + 1,
                "total": 0 if writing else len(getattr(plan, "steps", []) or []),
                "instruction": _instruction(plan, getattr(runner, "index", -1))}

    def workforce(self):
        """The agent workforce, once it has started - or None."""
        return getattr(self.manager, "agents", None)

    def agents(self) -> list[dict]:
        """The agents working right now, as the page shows them."""
        wf = self.workforce()
        if wf is None:
            return []
        try:
            states = wf.list_agents(active_only=True)
        except Exception:  # noqa: BLE001
            _log.debug("could not list agents", exc_info=True)
            return []
        out = []
        for s in states:
            out.append({
                "title": s.current_task_title or getattr(s.spec, "name", "") or "Agent",
                "doing": s.current_action or "",
                "step": s.step_index, "total": s.step_total,
                "progress": float(s.progress or 0.0),
                "status": str(getattr(s.status, "value", s.status)),
            })
        return out

    def today(self) -> dict:
        """Today, counted from the journal. Nothing without a running app -
        a window opened on its own has no day to count."""
        if self.manager is None:
            return {"questions": 0, "walkthroughs": 0, "due": 0}
        from tutor_features import journal
        try:
            entries = journal.entries_today()
            due = journal.due_count()
        except Exception:  # noqa: BLE001
            return {"questions": 0, "walkthroughs": 0, "due": 0}
        walks = sum(1 for e in entries if journal.WALKTHROUGH_TAG in (e.get("tags") or ""))
        return {"questions": len(entries) - walks, "walkthroughs": walks, "due": due}

    # ── switches: read from config, changed through the tray ────────────────

    def switch(self, name: str) -> bool:
        from config import cfg
        if name == "quiz_mode":
            return bool(getattr(self.manager, "_quiz_mode", False))
        if name == "wake_word":
            return cfg.ambient_mic()
        return cfg.switch(name)

    def set_switch(self, name: str, on: bool) -> None:
        if self.tray is not None and hasattr(self.tray, "apply_switch"):
            self.tray.apply_switch(name, bool(on))
        self.changed.emit("switches")

    # ── settings (APP-11) ────────────────────────────────────────────────────

    def instructions(self) -> str:
        """The standing instructions Tora follows ("only help with Excel")."""
        from config import cfg
        return cfg.custom_instructions or ""

    def set_instructions(self, text: str) -> None:
        if self.tray is not None:
            self.tray.on_set_custom_instructions.emit((text or "").strip())

    # Not microphones: Windows' routing entries and nameless endpoints.
    _NOT_MICS = ("sound mapper", "primary sound capture", "input ()", "input (@")

    def mics(self) -> list[tuple[int, str, bool]]:
        """The microphones, once each: (index, name, is the system default).

        Windows lists every input once per audio system (MME, DirectSound,
        WASAPI, WDM-KS) - one headset came out as five rows, beside "Sound
        Mapper" and "Input ()". Only the default microphone's own audio
        system is listed, without its routing entries, each name once."""
        try:
            import sounddevice as sd
            default = sd.default.device[0]
            devices = sd.query_devices()
            api = devices[default]["hostapi"] if default is not None and default >= 0 else None
            out, seen = [], set()
            for i, d in enumerate(devices):
                name = str(d.get("name", "")).strip()
                if d.get("max_input_channels", 0) <= 0 or (api is not None and d["hostapi"] != api):
                    continue
                if not name or any(bad in name.lower() for bad in self._NOT_MICS) or name in seen:
                    continue
                seen.add(name)
                out.append((i, name, i == default))
            return out
        except Exception:  # noqa: BLE001
            return []

    def mic_index(self) -> Optional[int]:
        """The device Tora listens on; None = the system default."""
        from config import cfg
        return cfg.mic_device()

    def set_mic(self, index: int) -> None:
        if self.tray is not None and hasattr(self.tray, "pick_mic"):
            self.tray.pick_mic(index)

    def panel_visible(self) -> bool:
        try:
            return bool(self.panel is not None and self.panel.isVisible())
        except Exception:  # noqa: BLE001
            return False

    def set_panel_visible(self, on: bool) -> None:
        if self.tray is not None:
            (self.tray.on_show_panel if on else self.tray.on_hide_panel).emit()

    def autostart(self) -> bool:
        from ui import autostart
        return autostart.enabled()

    def set_autostart(self, on: bool) -> bool:
        from ui import autostart
        return autostart.set_enabled(on)

    def cursor_colour(self) -> str:
        """The colour key Tora wears (ui/tokens.CURSOR_COLOURS)."""
        from config import cfg
        return cfg.cursor_colour()

    def set_cursor_colour(self, key: str) -> None:
        """Save it and repaint her at once - the overlay reads it per frame."""
        from config import cfg
        from ui import tokens
        cfg.set_cursor_colour(key)
        try:
            from ui.overlay import set_cursor_colour
            set_cursor_colour(tokens.cursor_hex(key))
        except Exception:  # noqa: BLE001 - no overlay (a window on its own)
            pass

    def listen_sound(self) -> str:
        from config import cfg
        return cfg.listen_sound()

    def set_listen_sound(self, key: str) -> None:
        """Save the choice and play it, so the choice is heard."""
        from config import cfg
        from ui import sounds
        cfg.set_listen_sound(key)
        sounds.play(key)

    def sidebar_collapsed(self) -> bool:
        from config import cfg
        return cfg.sidebar_collapsed()

    def set_sidebar_collapsed(self, shut: bool) -> None:
        from config import cfg
        cfg.set_sidebar_collapsed(shut)

    def uses(self) -> list[str]:
        """What they said they do at first launch ("Accounting", "Student"...)."""
        from config import cfg
        return cfg.profile()[1]

    def user_name(self) -> str:
        """The name given at first launch, for Home's greeting and the
        profile card ("" when none was)."""
        from config import cfg
        return cfg.profile()[0]

    def theme(self) -> str:
        """Settings > Appearance: "system" | "light" | "dark"."""
        from config import cfg
        return cfg.theme()

    def set_theme(self, mode: str) -> None:
        """Save the choice and restyle every open surface now."""
        from config import cfg
        from ui import app_theme
        cfg.set_theme(mode)
        app_theme.refresh(cfg.theme())

    # ── the agents' rails (Settings > Agents) ────────────────────────────────

    def agent_rails(self) -> dict:
        """What the agents may do and how much: the same values the
        workforce's own settings panel edits (agents/ui/settings.py)."""
        from config import cfg
        wf = self.workforce()
        workspace = (getattr(cfg, "agent_workspace", "") or "").strip() or (
            getattr(wf, "workspace_root", "") if wf is not None else "")
        return {"workspace": workspace,
                "risk": str(getattr(cfg, "agent_auto_risk", "medium") or "medium").lower(),
                "plan": bool(getattr(cfg, "agent_plan_approval", False)),
                "at_once": int(getattr(cfg, "agent_max_concurrent", 3) or 0),
                "minutes": int(getattr(cfg, "agent_wall_clock_s", 1800) or 0) // 60,
                "tokens": int(getattr(cfg, "agent_max_tokens", 300000) or 0)}

    def set_agent_rail(self, name: str, value) -> None:
        """Save one rail and put it on the running agents at once - through
        the panel's own apply_to, so the two can never disagree."""
        from config import cfg
        setter = {"workspace": cfg.set_agent_workspace, "risk": cfg.set_agent_auto_risk,
                  "plan": cfg.set_agent_plan_approval, "at_once": cfg.set_agent_max_concurrent,
                  "minutes": lambda m: cfg.set_agent_wall_clock_s(int(m) * 60),
                  "tokens": cfg.set_agent_max_tokens}[name]
        setter(value)
        r = self.agent_rails()
        try:
            from agents.ui.settings import AgentSettingsDialog
            AgentSettingsDialog.apply_to(self.workforce(), workspace=r["workspace"], risk=r["risk"],
                                         plan=r["plan"], limit=r["at_once"],
                                         wall_clock_s=r["minutes"] * 60, max_tokens=r["tokens"])
        except Exception:  # noqa: BLE001
            _log.debug("could not apply the agents' rails", exc_info=True)
        self.changed.emit("switches")

    def endpoints(self) -> list[dict]:
        """Where the agents' requests go: each connected endpoint."""
        try:
            from agents.providers import AccountStore
            accounts = [a for a in AccountStore().list() if not a.kinds]
        except Exception:  # noqa: BLE001
            return []
        out = [{"name": a.label or a.provider, "url": a.base_url or "", "on": bool(a.enabled)}
               for a in accounts]
        if not out:
            # As the editor does: an endpoint set up in .env shows, saved or not.
            import os
            base = os.environ.get("TORA_OMNIROUTE_BASE_URL", "")
            if base:
                out = [{"name": "OmniRoute", "url": base, "on": True}]
        return out

    def clear_agent_history(self) -> int:
        """Forget finished jobs (the files they made stay)."""
        wf = self.workforce()
        if wf is None:
            return 0
        from agents.ui.settings import AgentSettingsDialog
        n = AgentSettingsDialog.clear_history(wf)
        self.changed.emit("agents")
        return n

    def auto_approve(self) -> bool:
        """Settings > Approvals: do agents and desktop actions go ahead
        without the approval box? The running workforce says; without one,
        what will apply when it starts."""
        wf = self.workforce()
        if wf is not None:
            return bool(getattr(wf, "auto_approve", False))
        from config import cfg
        return cfg.switch("auto_approve")

    def set_auto_approve(self, on: bool) -> None:
        """Saved for every launch, and applied to the running agents now -
        the next approval an agent would have asked for goes ahead."""
        from config import cfg
        cfg.set_switch("auto_approve", bool(on))
        wf = self.workforce()
        if wf is not None and hasattr(wf, "set_auto_approve"):
            wf.set_auto_approve(bool(on))
        self.changed.emit("switches")

    # ── HeyTora account (2026-10-01) ─────────────────────────────────────────

    def account_state(self) -> dict:
        """What the Account card shows - never the network. The plan is the
        account's own (account.service.plan_state): the last one read, or the
        one remembered from the last run, whether it is being read again, and
        why the last read failed. The card redraws when any of it changes."""
        from account import settings
        from account.service import account
        from config import cfg
        acc = account()
        self._watch_account()
        st = acc.plan_state()
        return {"configured": settings.configured(), "mode": cfg.access_mode(),
                "signed_in": acc.signed_in(), "email": acc.email,
                "website": settings.website_url(), "summary": st["summary"],
                "fresh": st["fresh"], "busy": st["busy"], "error": st["error"],
                "own_key": bool(cfg.google_api_key)}

    def refresh_account(self, force: bool = False) -> None:
        """Read the plan and this month's use again, off the UI thread, unless
        it was read in the last minute (or `force`). The card hears the
        answer - or the failure - through the "account" topic."""
        from account.service import account
        self._watch_account()
        account().refresh_soon(0 if force else 60)

    # ── updates (updates/service.py) ───────────────────────────────────────

    def update_state(self) -> dict:
        """What the Updates card shows - never the network."""
        from config import cfg
        from updates import updater
        self._watch_updates()
        st = updater().state()
        st["auto"] = cfg.switch("auto_update")
        return st

    def check_updates(self) -> None:
        from updates import updater
        self._watch_updates()
        updater().check_soon(force=True)

    def install_update(self) -> None:
        """Restart to update - the tray's path, so main.py closes HeyTora."""
        if self.tray is not None and hasattr(self.tray, "on_install_update"):
            self.tray.on_install_update.emit()

    def set_auto_update(self, on: bool) -> None:
        from config import cfg
        cfg.set_switch("auto_update", bool(on))
        self.changed.emit("updates")

    def open_download_page(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from updates.service import DOWNLOAD_PAGE
        QDesktopServices.openUrl(QUrl(DOWNLOAD_PAGE))

    def _watch_updates(self) -> None:
        """The updater's changes (any thread) become the "updates" topic."""
        if getattr(self, "_watching_updates", False):
            return
        self._watching_updates = True
        import weakref
        from updates import updater
        me = weakref.ref(self)

        def heard() -> None:
            bridge = me()
            if bridge is not None:
                try:
                    bridge.changed.emit("updates")
                except RuntimeError:
                    pass
        updater().on_change(heard)

    def _watch_account(self) -> None:
        """The account's plan changes (any thread) become the "account" topic.
        Held weakly: a bridge that is gone is simply not told."""
        if getattr(self, "_watching", False):
            return
        self._watching = True
        import weakref
        from account.service import account
        me = weakref.ref(self)

        def heard() -> None:
            bridge = me()
            if bridge is not None:
                try:
                    bridge.changed.emit("account")
                except RuntimeError:          # its Qt object is already deleted
                    pass
        account().on_change(heard)

    def account_sign_out(self) -> None:
        """Sign out - and with no account, no HeyTora: the app restarts at the
        sign-in window (main.py, ui/sign_in_gate.py)."""
        from account.service import account
        account().sign_out()
        self._access_changed()
        from ui import sign_in_gate
        sign_in_gate.lost()

    def delete_account(self, confirm_email: str) -> str:
        """Close the account on HeyTora's server. A network call - run it off
        the UI thread. "" when it is gone, else why not, in words."""
        from account.service import AccountError, account
        try:
            account().delete_account(confirm_email)
        except AccountError as e:
            return str(e) or "HeyTora's server had a problem - try again."
        except Exception:  # noqa: BLE001
            _log.exception("could not delete the account")
            return "Something went wrong - try again."
        return ""

    def account_deleted(self) -> None:
        """After delete_account, on the UI thread: as after signing out, the
        app goes back to the sign-in window."""
        self._access_changed()
        from ui import sign_in_gate
        sign_in_gate.lost()

    def set_access_mode(self, mode: str) -> None:
        """"account" or "own_key", for every launch, and taken up now."""
        from config import cfg
        cfg.set_access_mode(mode)
        self._access_changed()

    def run_setup(self) -> None:
        """Open the first-launch wizard again (sign in, or add a key)."""
        if self.tray is not None and hasattr(self.tray, "on_run_setup"):
            self.tray.on_run_setup.emit()

    def open_website(self) -> None:
        from account import settings
        if settings.website_url():
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(settings.website_url()))

    def _access_changed(self) -> None:
        if self.manager is not None and hasattr(self.manager, "on_access_changed"):
            try:
                self.manager.on_access_changed()
            except Exception:  # noqa: BLE001
                _log.exception("could not take up the new access")
        self.changed.emit("account")

    def grants(self) -> list[str]:
        """Desktop actions the user said to always allow (DC-16)."""
        wf = self.workforce()
        try:
            return list(wf.desktop_grants()) if wf is not None else []
        except Exception:  # noqa: BLE001
            return []

    def forget_grant(self, key: str) -> None:
        wf = self.workforce()
        if wf is not None:
            wf.revoke_desktop_action(key)
        self.changed.emit("grants")

    # ── voice & models (APP-12) ──────────────────────────────────────────────

    def voices(self) -> tuple[str, ...]:
        from audio.tts.gemini_tts import VOICES
        return tuple(VOICES)

    def voice(self) -> str:
        from config import cfg
        return cfg.voice or ""

    def set_voice(self, name: str) -> None:
        if self.tray is not None and hasattr(self.tray, "pick_voice"):
            self.tray.pick_voice(name)

    def route(self) -> str:
        """"realtime" (the live session) or "cascade"."""
        from config import cfg
        return "realtime" if cfg.realtime() else "cascade"

    def set_route(self, mode: str) -> None:
        if self.tray is not None and hasattr(self.tray, "pick_route"):
            self.tray.pick_route(mode)

    def models(self) -> dict[str, str]:
        """Which model does each job, by role."""
        from ai.gemini import Models
        return Models.all_roles()

    def refresh_models(self) -> None:
        if self.tray is not None:
            self.tray.on_refresh_models.emit()

    # ── setup (APP-12) ───────────────────────────────────────────────────────

    def keys(self) -> list[dict]:
        """Each API key: its name, whether it is set, and its last four
        characters - never the key itself."""
        from config import cfg
        out = []
        for env, (_attr, label) in cfg.API_KEY_FIELDS.items():
            value = cfg.get_api_key(env) or ""
            out.append({"env": env, "label": label, "set": bool(value),
                        "tail": value[-4:] if len(value) >= 8 else ""})
        return out

    def open_keys(self) -> None:
        if self.tray is not None:
            self.tray.on_api_keys.emit()

    def diagnostics(self) -> list[tuple[str, str, str]]:
        """What is working right now: (name, detail, "ok" / "warn" / "bad").
        All local - nothing here makes a request."""
        from ai.gemini import Models
        from config import cfg
        rows = []
        mic = self.mic_name()
        rows.append(("Microphone", mic or "No input device found", "ok" if mic else "bad"))
        problem = self.problem()
        if problem:
            rows.append(("Live session", "Refused - out of Gemini credit" if problem == "billing"
                         else "Refused - the API key", "bad"))
        elif self.live():
            rows.append(("Live session", f"Open · {Models.REALTIME}", "ok"))
        elif not cfg.has_key():
            rows.append(("Live session", "No Google API key", "bad"))
        else:
            rows.append(("Live session", "Closed - it opens with the next question", "warn"))
        wf = self.workforce()
        if wf is not None:
            try:
                n = len(wf.list_agents(active_only=True))
                rows.append(("Agent workforce", f"Ready · {n} working now", "ok"))
            except Exception:  # noqa: BLE001
                rows.append(("Agent workforce", "Ready", "ok"))
        else:
            rows.append(("Agent workforce", "Did not start - see tora.log", "bad"))
        try:
            from PyQt6.QtWidgets import QApplication
            screens = QApplication.screens()
            scales = ", ".join(f"{round(s.devicePixelRatio() * 100)}%" for s in screens)
            rows.append(("Screens", f"{len(screens)} · scale {scales}", "ok" if screens else "bad"))
        except Exception:  # noqa: BLE001
            pass
        try:
            import importlib.util
            ocr = importlib.util.find_spec("rapidocr_onnxruntime") is not None
        except Exception:  # noqa: BLE001
            ocr = False
        rows.append(("Local OCR", "RapidOCR ready" if ocr else
                     "Not installed - reading fine print is off", "ok" if ocr else "warn"))
        return rows

    def run_setup(self) -> None:
        if self.tray is not None:
            self.tray.on_run_setup.emit()

    def save_report(self) -> None:
        if self.tray is not None:
            self.tray.on_diagnostics.emit()

    def quit(self) -> None:
        if self.tray is not None:
            self.tray.on_quit.emit()

    # ── journal (APP-15) ─────────────────────────────────────────────────────

    def _journal(self):
        """The journal - or None for a window opened on its own, which has
        no user to read (see today())."""
        if self.manager is None:
            return None
        from tutor_features import journal
        return journal

    def _read_journal(self, what: str, default, *args):
        journal = self._journal()
        if journal is None:
            return default
        try:
            return getattr(journal, what)(*args)
        except Exception:  # noqa: BLE001 - a locked or broken db is not a crash
            _log.debug("could not read the journal (%s)", what, exc_info=True)
            return default

    def due(self, limit: int = 5) -> list[dict]:
        """Entries due for review now, the longest-waiting first."""
        return self._read_journal("due_for_review", [], limit)

    def due_count(self) -> int:
        return self._read_journal("due_count", 0)

    def next_due(self) -> Optional[float]:
        """When the next entry comes back, if nothing is due now."""
        return self._read_journal("next_due_at", None)

    def recent(self, limit: int = 8) -> list[dict]:
        """The latest questions and walkthroughs, newest first."""
        return self._read_journal("recent", [], limit)

    def stats(self) -> dict:
        """Home's numbers, all counted from the journal: questions asked,
        walkthroughs, and the day streak - consecutive days with at least
        one question, up to today (or yesterday, if today has none yet)."""
        journal = self._journal()
        none = {"questions": 0, "walkthroughs": 0, "streak": 0}
        if journal is None:
            return none
        try:
            rows = journal.activity()
        except Exception:  # noqa: BLE001
            return none
        from datetime import date, timedelta
        walks = sum(1 for _t, tags in rows if journal.WALKTHROUGH_TAG in tags)
        days = {date.fromtimestamp(t) for t, _tags in rows}
        day = date.today()
        if day not in days:
            day -= timedelta(days=1)
        streak = 0
        while day in days:
            streak += 1
            day -= timedelta(days=1)
        return {"questions": len(rows) - walks, "walkthroughs": walks, "streak": streak}

    def insights(self) -> dict:
        """The Insights page's numbers (ui/insights_data.py), all measured:
        the journal's questions, apps, walkthroughs and reviews; this
        month's agent jobs; the plan's month."""
        from ui import insights_data as data
        journal = self._journal()
        rows, seconds, tag = [], 0.0, "walkthrough"
        if journal is not None:
            tag = journal.WALKTHROUGH_TAG
            try:
                rows, seconds = journal.insight_rows(), journal.walkthrough_seconds()
            except Exception:  # noqa: BLE001
                _log.debug("could not read the journal for insights", exc_info=True)
        out = data.summarise(rows, seconds, tag)
        tasks = self.tasks(500)
        out["agents"] = data.agents_month(tasks)
        walks = []
        if journal is not None:
            try:
                walks = journal.walkthrough_times()
            except Exception:  # noqa: BLE001
                walks = []
        out["time"] = data.time_worked(walks, tasks)
        out["handled"] = data.handled(out, tasks)
        out["knows"] = self._knows()
        try:
            st = self.account_state()
            summary = st.get("summary") if st.get("signed_in") and st.get("mode") == "account" else None
        except Exception:  # noqa: BLE001
            summary = None
        out["plan"] = data.plan(summary)
        out["plan_summary"] = summary
        out["due"] = self.due_count()
        return out

    def _knows(self) -> dict:
        """What Tora remembers about the user (ai/gemini_memory.py): the
        facts the remember tool kept, the two newest, and attached documents."""
        if self.manager is None:
            return {"facts": 0, "documents": 0, "recent": []}
        try:
            from ai import gemini_memory
            st = gemini_memory.stats()
            return {"facts": st["facts"], "documents": st["documents"],
                    "recent": gemini_memory.recent_facts(2)}
        except Exception:  # noqa: BLE001
            _log.debug("could not read Tora's memory", exc_info=True)
            return {"facts": 0, "documents": 0, "recent": []}

    def search(self, text: str, limit: int = 100) -> list[dict]:
        """Everything asked that mentions `text`, newest first."""
        return self._read_journal("search", [], text, limit)

    def review(self, entry_id: int, knew: bool) -> None:
        """Grade one review: knew it - it comes back later; not yet - tomorrow."""
        journal = self._journal()
        if journal is not None:
            try:
                journal.mark_reviewed(int(entry_id), bool(knew))
            except Exception:  # noqa: BLE001
                _log.warning("could not save a review", exc_info=True)
        self.changed.emit("journal")

    def export_history(self, path: str) -> int:
        """Everything the journal keeps, as one JSON file (Data & privacy)."""
        journal = self._journal()
        if journal is None:
            from tutor_features import journal
        return journal.export_all(path)

    def delete_history(self) -> int:
        """Delete everything the journal keeps. The caller has asked first."""
        journal = self._journal()
        if journal is None:
            from tutor_features import journal
        n = journal.clear_all()
        self.changed.emit("journal")
        return n

    def open_web(self, path: str) -> None:
        """A page of heytora.com - the privacy policy, the terms."""
        from account import settings
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(settings.website_url() + path))

    def set_user_name(self, name: str) -> None:
        """The name Tora greets you by, from first launch - changed in Account."""
        from config import cfg
        _old, uses = cfg.profile()
        cfg.set_profile(name, uses)

    def version(self) -> str:
        from version import __version__
        return __version__

    def open_journal_folder(self) -> None:
        if self.tray is not None:
            self.tray.on_journal_open.emit()

    def attach_document(self) -> None:
        if self.tray is not None:
            self.tray.on_attach_doc.emit()

    def recording(self) -> bool:
        """Is a lesson being recorded right now?"""
        try:
            return bool(getattr(self.manager, "is_recording", False))
        except Exception:  # noqa: BLE001
            return False

    def set_recording(self, on: bool) -> None:
        # The tray's own handlers: they say where the video went, and the
        # manager's sig_recording_state redraws the page ("recording").
        if self.tray is not None:
            (self.tray.on_record_start if on else self.tray.on_record_stop).emit()

    def capturing(self) -> bool:
        """Is Workflow Capture recording clicks and keys right now?"""
        capture = getattr(self.manager, "_workflow", None)
        return bool(capture is not None and getattr(capture, "is_running", False))

    def set_capturing(self, on: bool) -> None:
        if self.tray is not None:
            (self.tray.on_workflow_start if on else self.tray.on_workflow_stop).emit()
        self.changed.emit("journal")          # capture has no signal of its own

    # ── agents (APP-17) ──────────────────────────────────────────────────────

    def tasks(self, limit: int = 8) -> list[dict]:
        """The latest tasks, newest first. A project's members are left to
        its lead, as the notifications do."""
        wf = self.workforce()
        if wf is None:
            return []
        try:
            tasks = wf.list_tasks()
        except Exception:  # noqa: BLE001
            _log.debug("could not list tasks", exc_info=True)
            return []
        out = []
        for t in tasks:
            if (t.metadata or {}).get("parent_id"):
                continue
            out.append({
                "id": t.id, "title": t.title or (t.goal or "")[:60] or "Task",
                "status": str(getattr(t.status, "value", t.status)),
                "error": (t.error or "").strip().split("\n")[0][:160],
                "when": float(t.updated_at or t.created_at or 0.0),
                "created": float(t.created_at or 0.0),
            })
            if len(out) >= limit:
                break
        return out

    def approvals(self) -> list[dict]:
        """What the agents are waiting for a yes on."""
        wf = self.workforce()
        if wf is None:
            return []
        try:
            pending = wf.pending_approvals()
        except Exception:  # noqa: BLE001
            return []
        out = []
        for req in pending:
            task = wf.get_task(req.task_id)
            out.append({"id": req.id, "tool": req.call.tool_name,
                        "reason": req.reason or req.call.tool_name,
                        "task": task.title if task else "",
                        "sensitive": bool(req.sensitive),
                        "confirm_word": getattr(req, "confirm_word", "") or ""})
        return out

    # ── the Agents page (DESIGN.md §3F) ──────────────────────────────────────

    def running(self) -> list[dict]:
        """Jobs on the go, top level only (a project's agents are its own),
        with where each one is: the step it is on, what it is doing."""
        wf = self.workforce()
        if wf is None:
            return []
        try:
            tasks = wf.list_tasks(active_only=True)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for t in tasks:
            if (t.metadata or {}).get("parent_id"):
                continue
            total = len(t.steps)
            done = sum(1 for st in t.steps if str(getattr(st.status, "value", st.status)) == "done")
            out.append({"id": t.id, "title": t.title or (t.goal or "")[:60] or "Job",
                        "doing": t.current_action or "",
                        "step": min(total, done + 1) if total else 0, "total": total,
                        "progress": float(t.progress or 0.0),
                        "status": str(getattr(t.status, "value", t.status)),
                        "started": float(t.created_at or 0.0)})
        return out

    def give_job(self, text: str) -> tuple[str, str]:
        """Hand the agents a job in the user's words. ("started", its title),
        ("chat", "") when it reads as a question rather than a job, or
        ("error", why)."""
        wf = self.workforce()
        if wf is None:
            return "error", "The agents did not start - see tora.log for why."
        try:
            task = wf.submit(text.strip())
        except Exception as e:  # noqa: BLE001
            _log.warning("could not start a job", exc_info=True)
            return "error", str(e) or type(e).__name__
        if task is None:
            return "chat", ""
        self.changed.emit("agents")
        return "started", task.title or text.strip()[:60]

    def _task_call(self, name: str, task_id: str) -> bool:
        wf = self.workforce()
        if wf is None:
            return False
        try:
            ok = bool(getattr(wf, name)(task_id))
        except Exception:  # noqa: BLE001
            _log.debug("could not %s %s", name, task_id, exc_info=True)
            return False
        self.changed.emit("agents")
        return ok

    def pause_job(self, task_id: str) -> bool:
        return self._task_call("pause", task_id)

    def resume_job(self, task_id: str) -> bool:
        return self._task_call("resume", task_id)

    def stop_job(self, task_id: str) -> bool:
        return self._task_call("cancel", task_id)

    def answer_approval(self, request_id: str, approved: bool, confirmation: str = "") -> bool:
        """Approve or deny what an agent is waiting on - the same answer the
        approval window gives; whichever comes first counts."""
        wf = self.workforce()
        if wf is None:
            return False
        try:
            ok = bool(wf.approve(request_id, approved, confirmation=confirmation))
        except Exception:  # noqa: BLE001
            _log.debug("could not answer %s", request_id, exc_info=True)
            return False
        self.changed.emit("agents")
        return ok

    def job(self, task_id: str) -> Optional[dict]:
        """One job, for its details: the goal, the plan step by step, how it
        ended, and the folder it worked in."""
        wf = self.workforce()
        task = wf.get_task(task_id) if wf is not None else None
        if task is None:
            return None
        steps = [{"title": st.title, "status": str(getattr(st.status, "value", st.status)),
                  "summary": st.summary or "", "error": st.error or ""} for st in task.steps]
        return {"id": task.id, "title": task.title or (task.goal or "")[:60], "goal": task.goal or "",
                "status": str(getattr(task.status, "value", task.status)),
                "doing": task.current_action or "", "steps": steps,
                "result": task.result or "", "error": (task.error or "").strip(),
                "folder": task.workspace or "", "started": float(task.created_at or 0.0),
                "updated": float(task.updated_at or 0.0),
                "skill": str((task.metadata or {}).get("skill") or "")}

    # ── skills (docs/agents/SKILLS.md) ───────────────────────────────────────

    def _skills(self):
        """The workforce's skill library - or one of its own when the agents
        did not start, so the page still shows and edits the files."""
        wf = self.workforce()
        lib = getattr(wf, "skills", None) if wf is not None else None
        if lib is None:
            from agents.skills import SkillLibrary
            lib = getattr(self, "_own_skills", None) or SkillLibrary()
            self._own_skills = lib
        return lib

    def skills(self) -> list[dict]:
        """Every skill, with when it last ran (from the job history)."""
        lib = self._skills()
        last: dict[str, dict] = {}
        wf = self.workforce()
        if wf is not None:
            try:
                for t in wf.list_tasks():               # newest first
                    sid = (t.metadata or {}).get("skill")
                    if sid and sid not in last:
                        last[sid] = {"at": float(t.updated_at or t.created_at or 0.0),
                                     "status": str(getattr(t.status, "value", t.status)), "job": t.id}
            except Exception:  # noqa: BLE001
                _log.debug("could not read the skills' runs", exc_info=True)
        return [{**s.to_dict(), "last": last.get(s.id)} for s in lib.all()]

    def skill_problems(self) -> dict[str, str]:
        """Skill files that were skipped, and why (read by skills())."""
        return dict(self._skills().problems)

    def skill(self, skill_id: str) -> Optional[dict]:
        s = self._skills().get(skill_id)
        return s.to_dict() if s is not None else None

    def save_skill(self, data: dict) -> str:
        """Save a skill from the editor. "" when saved, else why not."""
        from agents.skills import Skill, SkillError
        from agents.skills.schedule import ScheduleError, parse_every
        asks = data.get("ask_before") or []
        if isinstance(asks, str):
            asks = [a.strip() for a in asks.replace(";", ",").split(",") if a.strip()]
        skill = Skill(id=str(data.get("id") or ""), name=str(data.get("name") or "").strip(),
                      when=str(data.get("when") or "").strip(), body=str(data.get("body") or ""),
                      apps=str(data.get("apps") or "").strip(), ask_before=list(asks),
                      every=str(data.get("every") or "").strip())
        if skill.every:
            try:
                parse_every(skill.every)
            except ScheduleError as e:
                return f"“Runs by itself” - {e}"
        try:
            self._skills().save(skill)
        except SkillError as e:
            return str(e).capitalize()
        except OSError as e:
            return f"It could not be saved: {e.strerror or e}"
        self._skills_changed()
        return ""

    def delete_skill(self, skill_id: str) -> bool:
        ok = self._skills().delete(skill_id)
        self._skills_changed()
        return ok

    def set_skill_enabled(self, skill_id: str, on: bool) -> None:
        self._skills().set_enabled(skill_id, bool(on))
        self._skills_changed()

    def _skills_changed(self) -> None:
        wf = self.workforce()
        if wf is not None:
            try:
                from agents.skills.schedule import sync_jobs
                sync_jobs(wf)
            except Exception:  # noqa: BLE001
                _log.warning("could not reschedule the skills", exc_info=True)
        self.changed.emit("skills")

    def browser_sites(self) -> list[dict]:
        """The sites the agents' browser is signed in to (SK-06)."""
        from agents.tools import browser_tools
        return browser_tools.signed_in_sites()

    def forget_browser_sites(self) -> str:
        """Sign the agents' browser out everywhere. "" when done, else why not."""
        from agents.tools import browser_tools
        return browser_tools.forget_sign_ins()

    def skill_from_job(self, task_id: str) -> Optional[dict]:
        """A first draft of a skill from a job that went well: its request
        as the 'when', its plan as the steps. The user reads and edits it
        before anything is saved (SK-05)."""
        job = self.job(task_id)
        if job is None:
            return None
        steps = [s["title"] for s in job["steps"] if s.get("status") != "skipped" and s.get("title")]
        body = "## Steps\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(steps, 1)) if steps else ""
        body += "\n\n## What to know\n- "
        goal = " ".join((job["goal"] or "").split())
        return {"id": "", "name": job["title"][:60], "when": f"the user asks for something like: {goal[:160]}",
                "body": body.strip() + " ", "apps": "", "ask_before": [], "every": ""}

    def open_folder(self, path: str) -> bool:
        """Open a job's folder in Explorer."""
        import os
        if not path or not os.path.isdir(path):
            return False
        try:
            os.startfile(path)        # noqa: S606 - the user's own folder, on their click
            return True
        except Exception:  # noqa: BLE001
            return False

    def stop_agents(self) -> None:
        if self.tray is not None:
            self.tray.on_stop_agents.emit()

    # ── doing ────────────────────────────────────────────────────────────────

    def show_panel(self) -> None:
        if self.tray is not None:
            self.tray.on_show_panel.emit()


def _instruction(plan, idx: int) -> str:
    steps = getattr(plan, "steps", None) or []
    return steps[idx].instruction if 0 <= idx < len(steps) else ""
