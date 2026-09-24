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

    # ── doing ────────────────────────────────────────────────────────────────

    def show_panel(self) -> None:
        if self.tray is not None:
            self.tray.on_show_panel.emit()


def _instruction(plan, idx: int) -> str:
    steps = getattr(plan, "steps", None) or []
    return steps[idx].instruction if 0 <= idx < len(steps) else ""
