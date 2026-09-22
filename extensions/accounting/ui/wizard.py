"""Setting up accounting agents: "Create an accounts payable agent for this client".

AgentSetupWizard walks the questions the spec lists - accounting system,
inbox, supplier folder, permission level, approval threshold, schedule -
and saves the agent into the pack's settings, where the scheduler picks
it up. NewClientDialog creates a client folder with its provider and
jurisdiction. Cloud providers are listed but disabled until Phase 2, so
the accountant sees the plan without being offered a button that fails.
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Optional

from PyQt6.QtCore import QTime
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QTimeEdit, QVBoxLayout, QWidget,
)

from .. import roles, workflows
from ..jurisdiction import PRESETS
from ..providers import INTEGRATIONS
from ..workspace import ClientWorkspace, find_client, list_clients

QSS = """
QDialog { background-color: rgb(18, 18, 22); }
QLabel { color: rgb(225, 225, 235); font-size: 12px; }
QLabel#wzTitle { font-size: 16px; font-weight: 600; }
QLabel#wzNote { color: rgb(150, 150, 170); font-size: 11px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit { background: rgba(34, 34, 42, 255); color: rgb(230, 230, 240);
    padding: 5px 8px; border-radius: 6px; border: 1px solid rgba(90, 90, 110, 150); }
QPushButton { font-size: 12px; padding: 6px 14px; border-radius: 8px; color: rgb(230, 230, 240);
              background-color: rgba(60, 60, 75, 110); border: 1px solid rgba(90, 90, 110, 150); }
QPushButton#wzPrimary { background-color: rgba(0, 120, 255, 120); border-color: rgba(0, 120, 255, 200); }
"""

SCHEDULES = [("Template default", ""), ("Manual only", "manual"), ("When new documents arrive", "folder"),
             ("Every weekday", "weekdays"), ("Every day", "daily"), ("Weekly", "weekly"), ("Monthly", "monthly")]


def _providers(box: QComboBox) -> None:
    for spec in sorted(INTEGRATIONS, key=lambda i: (not i.available, i.rank)):
        box.addItem(spec.name + ("" if spec.available else "  (Phase 2)"), spec.id)
        if not spec.available:
            box.model().item(box.count() - 1).setEnabled(False)


class NewClientDialog(QDialog):
    def __init__(self, ext, ctx, parent: Optional[QWidget] = None, *, name: str = ""):
        super().__init__(parent)
        self.ext, self.ctx = ext, ctx
        self.created: Optional[ClientWorkspace] = None
        self.setWindowTitle("New client")
        self.setStyleSheet(QSS)
        form = QFormLayout(self)
        self.name = QLineEdit(name)
        form.addRow("Client name", self.name)
        self.provider = QComboBox()
        _providers(self.provider)
        self.provider.setCurrentIndex(max(0, self.provider.findData("local")))
        form.addRow("Accounting system", self.provider)
        self.jurisdiction = QComboBox()
        for code, prof in PRESETS.items():
            self.jurisdiction.addItem(f"{prof.country} ({prof.tax_label} {prof.tax_rate:g}%)", code)
        self.jurisdiction.setCurrentIndex(max(0, self.jurisdiction.findData(
            ctx.settings.get("default_jurisdiction") or "generic")))
        form.addRow("Jurisdiction", self.jurisdiction)
        self.period = QLineEdit()
        self.period.setPlaceholderText("YYYY-MM (default: this month)")
        form.addRow("Current period", self.period)
        note = QLabel("Jurisdiction presets are typical values to start from - edit them in the client's "
                      "accounting.json. The agents never give tax or legal advice.")
        note.setObjectName("wzNote")
        note.setWordWrap(True)
        form.addRow(note)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Create")
        ok.setObjectName("wzPrimary")
        ok.clicked.connect(self._create)
        row.addWidget(cancel)
        row.addWidget(ok)
        form.addRow(row)

    def _create(self) -> None:
        name = self.name.text().strip()
        if not name:
            return
        period = self.period.text().strip()
        if period and not re.fullmatch(r"\d{4}-\d{2}", period):
            self.period.setStyleSheet("border-color: rgb(255, 90, 90);")
            return
        self.created = self.ext.create_client(self.ctx, name, provider=self.provider.currentData(),
                                              jurisdiction=self.jurisdiction.currentData(), period=period)
        self.accept()


class AgentSetupWizard(QDialog):
    """Create a scheduled accounting agent for a client."""

    def __init__(self, manager, ext, *, client: str = "", template: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.mgr, self.ext = manager, ext
        self.ctx = manager.extensions._context("accounting")
        self.client_name = ""
        self.setWindowTitle("New accounting agent")
        self.setStyleSheet(QSS)
        self.resize(560, 560)
        root = QVBoxLayout(self)
        title = QLabel("New accounting agent")
        title.setObjectName("wzTitle")
        root.addWidget(title)
        form = QFormLayout()
        root.addLayout(form)

        self.template = QComboBox()
        for t in roles.TEMPLATES:
            self.template.addItem(t.name, t.id)
        if template:
            self.template.setCurrentIndex(max(0, self.template.findData(template)))
        self.template.currentIndexChanged.connect(self._template_changed)
        form.addRow("Agent", self.template)
        self.mission = QLabel("")
        self.mission.setObjectName("wzNote")
        self.mission.setWordWrap(True)
        form.addRow(self.mission)

        self.client = QComboBox()
        self.client.setEditable(True)
        for c in list_clients(ext.clients_root(self.ctx)):
            self.client.addItem(c.client, c.root)
        if client:
            self.client.setCurrentText(client)
        form.addRow("Client", self.client)
        self.provider = QComboBox()
        _providers(self.provider)
        form.addRow("Accounting system", self.provider)

        inbox_row = QHBoxLayout()
        self.inbox = QLineEdit()
        self.inbox.setPlaceholderText("default: the client's Inbox folder")
        browse = QPushButton("…")
        browse.clicked.connect(lambda: self._browse(self.inbox))
        inbox_row.addWidget(self.inbox)
        inbox_row.addWidget(browse)
        form.addRow("Invoice inbox folder", inbox_row)
        email = QLabel("Email inboxes (Gmail / Outlook) arrive in Phase 2 - until then, save attachments to the "
                       "inbox folder (Outlook rules can do this automatically).")
        email.setObjectName("wzNote")
        email.setWordWrap(True)
        form.addRow(email)
        sup_row = QHBoxLayout()
        self.suppliers = QLineEdit()
        self.suppliers.setPlaceholderText("optional: where supplier statements arrive")
        b2 = QPushButton("…")
        b2.clicked.connect(lambda: self._browse(self.suppliers))
        sup_row.addWidget(self.suppliers)
        sup_row.addWidget(b2)
        form.addRow("Supplier folder", sup_row)

        self.level = QComboBox()
        self.level.addItem("Prepare drafts (recommended)", "draft")
        self.level.addItem("Read and report only", "read")
        form.addRow("Permission level", self.level)
        posting = QLabel("Posting to the books always asks you. Paying suppliers and filing returns always "
                         "ask you to type a confirm word. No setting changes that.")
        posting.setObjectName("wzNote")
        posting.setWordWrap(True)
        form.addRow(posting)

        thr = QHBoxLayout()
        self.auto = QSpinBox()
        self.auto.setRange(50, 100)
        self.auto.setSuffix("%")
        self.auto.setValue(int(float(self.ctx.settings.get("confidence_auto", 0.95)) * 100))
        self.flag = QSpinBox()
        self.flag.setRange(0, 100)
        self.flag.setSuffix("%")
        self.flag.setValue(int(float(self.ctx.settings.get("confidence_flag", 0.70)) * 100))
        thr.addWidget(QLabel("draft at"))
        thr.addWidget(self.auto)
        thr.addWidget(QLabel("flag at"))
        thr.addWidget(self.flag)
        thr.addStretch(1)
        form.addRow("Approval threshold", thr)

        sch = QHBoxLayout()
        self.schedule = QComboBox()
        for label, key in SCHEDULES:
            self.schedule.addItem(label, key)
        self.at = QTimeEdit(QTime(8, 0))
        self.day = QSpinBox()
        self.day.setRange(1, 28)
        self.day.setPrefix("day ")
        self.weekday = QComboBox()
        self.weekday.addItems(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        for w in (self.schedule, self.at, self.weekday, self.day):
            sch.addWidget(w)
        form.addRow("Schedule", sch)
        self.sched_note = QLabel("")
        self.sched_note.setObjectName("wzNote")
        form.addRow(self.sched_note)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        create = QPushButton("Create")
        create.clicked.connect(lambda: self._create(run_now=False))
        run = QPushButton("Create and run now")
        run.setObjectName("wzPrimary")
        run.clicked.connect(lambda: self._create(run_now=True))
        row.addWidget(cancel)
        row.addWidget(create)
        row.addWidget(run)
        root.addStretch(1)
        root.addLayout(row)
        self._template_changed()

    def _browse(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text() or os.path.expanduser("~"))
        if path:
            edit.setText(path)

    def _template_changed(self) -> None:
        t = roles.BY_ID[self.template.currentData()]
        self.mission.setText(f"{t.mission}\nHandles: {', '.join(t.handles)}.")
        wf = workflows.BY_ID[t.default_workflow]
        self.sched_note.setText(f"Default: {wf.name} - {wf.trigger.describe()}")

    def _create(self, *, run_now: bool) -> None:
        t = roles.BY_ID[self.template.currentData()]
        name = self.client.currentText().strip()
        if not name:
            return
        ws = find_client(self.ext.clients_root(self.ctx), name)
        if ws is None or ws.client.lower() != name.lower() and not name.lower() in ws.client.lower():
            ws = self.ext.create_client(self.ctx, name, provider=self.provider.currentData() or "local")
        ws.confidence = {"auto": self.auto.value() / 100, "flag": min(self.flag.value(), self.auto.value()) / 100}
        if self.suppliers.text().strip():
            ws.provider_settings = {**ws.provider_settings, "supplier_folder": self.suppliers.text().strip()}
        ws.save()
        key = self.schedule.currentData()
        schedule: dict = {}
        if key == "manual":
            schedule = {"kind": "manual"}
        elif key == "folder":
            schedule = {"kind": "folder"}
        elif key:
            schedule = {"every": key, "at": self.at.time().toString("HH:mm"),
                        "weekday": self.weekday.currentIndex(), "day": self.day.value()}
        agent = {"id": uuid.uuid4().hex[:8], "template": t.id, "client": ws.client, "schedule": schedule,
                 "level": self.level.currentData(), "enabled": key != "manual",
                 "inbox": self.inbox.text().strip()}
        agents = list(self.ctx.settings.get("agents") or []) + [agent]
        self.mgr.extensions.configure("accounting", {"agents": agents, "active_client": ws.client})
        self.client_name = ws.client
        if run_now:
            task, spec = self.ext.task_for(t.id, ws.root)
            self.mgr.submit_task(task, agent=spec)
        self.accept()
