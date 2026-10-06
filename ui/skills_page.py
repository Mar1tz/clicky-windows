"""The Tora page's Skills tab: how you like jobs done, as text she follows
(docs/agents/SKILLS.md).

Skills are about her, not a separate app - so they sit on the Tora page
beside "Teach her", not in a sidebar row of their own. A list (the starter
skills, then yours), a switch on each, when it last ran, and one editor:
a name, when to use it, and one big box for the steps and what to know.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ui import app_theme as T
from ui.app_widgets import Card, Pill, Switch, button, label


def skills_card(bridge, navigate_edit: Callable[[Optional[dict]], None]) -> QWidget:
    """The list of skills. `navigate_edit(skill or None)` opens the editor."""
    from ui.app_parts import HoverRow, ListBox, RowAction, empty
    add = button("New skill", "primary")
    add.clicked.connect(lambda: navigate_edit(None))
    card = Card("Her skills", "How you like jobs done. Say the job - “invoice Sarah for last "
                              "week” - and she does it your way, in the background.", action=add)
    skills = bridge.skills()
    if not skills:
        card.body.addWidget(empty("No skills yet", "Add one: what the job is, and how you do it."))
        return card
    box = ListBox()
    for s in skills:
        box.add(_row(bridge, s, navigate_edit))
    card.body.addWidget(box)
    for name, why in sorted(bridge.skill_problems().items()):
        card.body.addWidget(label(f"{name} was skipped: {why}.", "setNote", wrap=True))
    return card


def _row(bridge, s: dict, navigate_edit) -> QWidget:
    from ui.app_parts import HoverRow, RowAction
    row = HoverRow()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(18, 14, 14, 14)
    lay.setSpacing(14)
    text = QVBoxLayout()
    text.setSpacing(3)
    head = QHBoxLayout()
    head.setSpacing(8)
    head.addWidget(label(s["name"], "rowTitle"))
    if s["source"] != "starter":
        head.addWidget(Pill("Yours" if s["source"] == "yours" else "Edited", T.ACCENT))
    head.addStretch(1)
    text.addLayout(head)
    text.addWidget(label("Used when " + s["when"].rstrip("."), "rowSub", wrap=True))
    facts = []
    if s.get("every"):
        facts.append(f"Runs by itself: {s['every']}")
    last = s.get("last")
    if last:
        facts.append(f"Last ran {_ago(last['at'])}" + (" - didn't finish" if last["status"] in
                                                         ("failed", "cancelled") else ""))
    if facts:
        text.addWidget(label(" · ".join(facts), "rowApp"))
    lay.addLayout(text, 1)
    row.actions_lay.addWidget(RowAction("pencil", "Edit", lambda: navigate_edit(s)))
    row.actions.setFixedWidth(32)
    row._show_actions(False)
    lay.addWidget(row.actions, 0, Qt.AlignmentFlag.AlignVCenter)
    switch = Switch(s["enabled"])
    switch.setAccessibleName(f"{s['name']} on")
    switch.toggled.connect(lambda on, sid=s["id"]: bridge.set_skill_enabled(sid, on))
    lay.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
    row.clicked.connect(lambda: navigate_edit(s))
    return row


def _ago(ts: float) -> str:
    gone = max(0.0, time.time() - ts)
    if gone < 90:
        return "just now"
    if gone < 3600:
        return f"{round(gone / 60)} min ago"
    if gone < 86400:
        return f"{round(gone / 3600)} h ago"
    days = round(gone / 86400)
    return "yesterday" if days == 1 else f"{days} days ago"


def open_editor(over: QWidget, bridge, skill: Optional[dict], saved: Callable[[], None]):
    """The editor, over `over` (the app window). `skill` None is a new one;
    a dict without an id is a draft (from a job) still to be saved."""
    from ui.settings_parts import SubDialog
    from ui.app_parts import growing_text
    new = not (skill and skill.get("id"))
    s = dict(skill or {"id": "", "name": "", "when": "", "body": "", "apps": "",
                       "ask_before": [], "every": "", "source": "yours"})
    dlg = SubDialog(over, "New skill" if new else s["name"],
                    "Write it like you'd explain it to a new assistant. She does the rest with "
                    "the apps you already use." if new else
                    ("A starter skill: saving keeps your copy; the original comes back if you "
                     "delete yours." if s.get("source") == "starter" else ""))
    dlg.W = 640

    def field(title: str, value: str, hint: str) -> QLineEdit:
        dlg.body.addWidget(label(title, "setTitle"))
        line = QLineEdit(value)
        line.setPlaceholderText(hint)
        dlg.body.addWidget(line)
        return line

    name = field("Name", s["name"], "Send an invoice")
    when = field("Use it when…", s["when"], "the user wants to invoice or bill a client")
    dlg.body.addWidget(label("Steps, and what she should know", "setTitle"))
    body = growing_text(s["body"], "1. Open Xero…\n2. One line per job, with the hours…\n\n"
                                   "Rates: design R650/hour…", least=150, most=300)
    dlg.body.addWidget(body)
    apps = field("Apps (optional)", s.get("apps", ""), "Xero, in the browser")
    asks = field("Always ask me before (optional)", ", ".join(s.get("ask_before") or []),
                 "sending, anything over R10,000")
    every = field("Runs by itself (optional)", s.get("every", ""),
                  "Friday 16:00 · weekdays 9am · new file in Downloads\\Receipts")
    problem = label("", "setNote", wrap=True)
    problem.hide()
    dlg.body.addWidget(problem)

    if not new and s.get("source") in ("yours", "edited"):
        gone = button("Delete" if s["source"] == "yours" else "Restore original", "danger")

        def _delete() -> None:
            bridge.delete_skill(s["id"])
            dlg.close_dialog()
            saved()
        gone.clicked.connect(_delete)
        dlg.foot.addWidget(gone)
    dlg.foot.addStretch(1)
    cancel = button("Cancel")
    cancel.clicked.connect(dlg.close_dialog)
    dlg.foot.addWidget(cancel)
    save = button("Save skill", "primary")

    def _save() -> None:
        why = bridge.save_skill({"id": s.get("id", ""), "name": name.text(), "when": when.text(),
                                 "body": body.toPlainText(), "apps": apps.text(),
                                 "ask_before": asks.text(), "every": every.text()})
        if why:
            problem.setText(why)
            problem.show()
            dlg._place()
            return
        dlg.close_dialog()
        saved()
    save.clicked.connect(_save)
    dlg.foot.addWidget(save)
    dlg.open()
    name.setFocus()
    return dlg
