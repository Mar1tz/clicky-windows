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


class AppBridge(QObject):
    """What the app window can read and do. Nothing else."""

    # Something the pages show has changed: "state", "account", "agents",
    # "guide", "switches". A page shown at the time refreshes itself.
    changed = pyqtSignal(str)

    def __init__(self, manager: Any = None, tray: Any = None, panel: Any = None):
        super().__init__()
        self.manager, self.tray, self.panel = manager, tray, panel
        self._state = "idle"
        if manager is not None:
            manager.sig_state_changed.connect(self._on_state)
            for signal, topic in (("sig_account_problem", "account"),
                                  ("sig_agent_event", "agents"),
                                  ("sig_agents_ready", "agents"),
                                  ("sig_guide_progress", "guide")):
                sig = getattr(type(manager), signal, None)
                if sig is not None:
                    getattr(manager, signal).connect(
                        lambda *_a, _t=topic: self.changed.emit(_t))

    def _on_state(self, state) -> None:
        self._state = str(getattr(state, "name", state) or "idle").lower()
        self.changed.emit("state")

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

    def mic_name(self) -> str:
        """The input device Tora is listening on, by name."""
        from config import cfg
        try:
            import sounddevice as sd
            index = cfg.mic_device_index
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
            due = len(journal.due_for_review(limit=100))
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

    def mics(self) -> list[tuple[int, str, bool]]:
        """Every input device: (index, name, is the system default)."""
        try:
            import sounddevice as sd
            default = sd.default.device[0]
            return [(i, str(d.get("name", "")), i == default)
                    for i, d in enumerate(sd.query_devices())
                    if d.get("max_input_channels", 0) > 0]
        except Exception:  # noqa: BLE001
            return []

    def mic_index(self) -> Optional[int]:
        """The device Tora listens on; None = the system default."""
        from config import cfg
        return cfg.mic_device_index

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

    # ── doing ────────────────────────────────────────────────────────────────

    def show_panel(self) -> None:
        if self.tray is not None:
            self.tray.on_show_panel.emit()


def _instruction(plan, idx: int) -> str:
    steps = getattr(plan, "steps", None) or []
    return steps[idx].instruction if 0 <= idx < len(steps) else ""
