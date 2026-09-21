"""Run the Active Agents panel against a real workforce, no app needed.

    python -m agents.ui.demo

Builds an in-memory AgentManager in a temp workspace, submits the two
demo tasks a few seconds apart, and shows the panel while they run. Use
it to check the visuals; the tests check the behaviour.
"""
from __future__ import annotations

import sys
import tempfile

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from agents.bootstrap import build_agent_manager
from agents.ui import open_agents_window

TASKS = [
    "Create a new folder called MyApp and build the foundation of a React application inside it.",
    "Create a folder called Notes",
]


def main() -> int:
    app = QApplication(sys.argv)
    workspace = tempfile.mkdtemp(prefix="tora-demo-")
    manager = build_agent_manager(workspace_root=workspace, in_memory=True).start()

    window = open_agents_window(manager)
    window.view_logs.connect(lambda task_id: print(f"[demo] logs requested for {task_id}"))
    print(f"[demo] workspace: {workspace}")

    for i, text in enumerate(TASKS):
        QTimer.singleShot(400 + i * 2500, lambda t=text: manager.submit(t))

    app.aboutToQuit.connect(lambda: manager.stop(timeout=3))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
