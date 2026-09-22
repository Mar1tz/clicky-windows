"""The Accounting dashboard module: one client at a glance, exceptions first.

Registered through IAgentExtension.get_dashboard_modules, drawn inside the
core's ExtensionDashboard, shown only while the pack is enabled.

    Header    client picker · period · currency · provider · month-end progress
    Overview  run buttons for every workflow, counts, where the files are
    Needs attention   open exceptions, most severe first; resolve with a note
    Drafts    everything prepared; post / release / submit (each goes through
              the approval dialog - the dashboard never bypasses it)
    Debtors & creditors · Deadlines · Agents (scheduled) · Reports
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import roles, workflows
from ..workspace import ClientWorkspace, list_clients

log = logging.getLogger("extensions.accounting.ui")

SEVERITY_COLOUR = {"high": QColor(255, 110, 100), "review": QColor(255, 190, 70), "info": QColor(150, 170, 200)}

QSS = """
QLabel#accTitle { font-size: 16px; font-weight: 600; color: rgb(240, 240, 245); }
QLabel#accMeta { font-size: 12px; color: rgb(170, 170, 190); }
QLabel#accBig { font-size: 22px; font-weight: 600; color: rgb(240, 240, 245); }
QLabel#accSmall { font-size: 11px; color: rgb(140, 140, 160); }
QListWidget, QTreeWidget { background-color: rgba(24, 24, 30, 230); color: rgb(225, 225, 235);
                           border: 1px solid rgba(60, 60, 75, 180); border-radius: 8px; font-size: 12px; }
QTreeWidget::item, QListWidget::item { padding: 4px; }
QHeaderView::section { background: rgba(34, 34, 42, 255); color: rgb(170, 170, 190); padding: 4px;
                       border: none; font-size: 11px; }
QComboBox { background: rgba(34, 34, 42, 255); color: rgb(230, 230, 240); padding: 5px 8px; border-radius: 6px;
            border: 1px solid rgba(90, 90, 110, 150); }
QProgressBar { background: rgba(34, 34, 42, 255); border-radius: 6px; height: 12px; text-align: center;
               color: rgb(230, 230, 240); font-size: 10px; }
QProgressBar::chunk { background-color: rgb(0, 140, 255); border-radius: 6px; }
"""

RUNS = [  # (button, template, workflow)
    ("Month-end close", "month_end", "month_end"),
    ("Process inbox", "bookkeeper", "invoice_processing"),
    ("Bookkeeping", "bookkeeper", "bookkeeping"),
    ("Debtors run", "accounts_receivable", "debtors_run"),
    ("Payables run", "accounts_payable", "ap_run"),
    ("Tax pack", "tax_preparation", "tax_prep"),
    ("Payroll review", "payroll_review", "payroll_review"),
    ("Management pack", "management_reporting", "management_reporting"),
    ("Audit pack", "audit_assistant", "audit_prep"),
]


class AccountingDashboard(QWidget):
    def __init__(self, manager, extension, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.mgr = manager
        self.ext = extension
        self.setStyleSheet(QSS)
        self._root = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("Accounting")
        title.setObjectName("accTitle")
        head.addWidget(title)
        self.client_box = QComboBox()
        self.client_box.setMinimumWidth(260)
        self.client_box.currentIndexChanged.connect(self._client_changed)
        head.addWidget(self.client_box)
        new_client = QPushButton("New client…")
        new_client.clicked.connect(self._new_client)
        head.addWidget(new_client)
        demo = QPushButton("Demo client")
        demo.setToolTip("ABC Manufacturing: three months of books with planted problems - safe to experiment on")
        demo.clicked.connect(self._demo_client)
        head.addWidget(demo)
        head.addStretch(1)
        new_agent = QPushButton("New agent…")
        new_agent.clicked.connect(self._new_agent)
        head.addWidget(new_agent)
        folder = QPushButton("Open folder")
        folder.clicked.connect(lambda: self._root and os.startfile(self._root))
        head.addWidget(folder)
        lay.addLayout(head)

        info = QHBoxLayout()
        self.meta = QLabel("")
        self.meta.setObjectName("accMeta")
        info.addWidget(self.meta, 1)
        self.me_label = QLabel("Month-end")
        self.me_label.setObjectName("accSmall")
        info.addWidget(self.me_label)
        self.me_bar = QProgressBar()
        self.me_bar.setMaximumWidth(220)
        info.addWidget(self.me_bar)
        lay.addLayout(info)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self._build_overview()
        self._build_attention()
        self._build_drafts()
        self._build_parties()
        self._build_deadlines()
        self._build_agents()
        self._build_reports()

        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.load_clients()

    # -- plumbing ------------------------------------------------------------------------------------
    @property
    def ctx(self):
        return self.mgr.extensions._context("accounting")

    def session(self):
        return self.ext.sessions.get(self._root) if self._root else None

    def _call(self, coro, timeout: float = 20):
        return self.mgr.runtime.call(coro, timeout=timeout)

    def load_clients(self, select: str = "") -> None:
        clients = list_clients(self.ext.clients_root(self.ctx))
        self.client_box.blockSignals(True)
        self.client_box.clear()
        for c in clients:
            self.client_box.addItem(c.client, c.root)
        self.client_box.blockSignals(False)
        want = select or self.ctx.settings.get("active_client") or ""
        idx = max(0, next((i for i, c in enumerate(clients) if c.client == want), 0))
        if clients:
            self.client_box.setCurrentIndex(idx)
            self._client_changed(idx)
        else:
            self._root = ""
            self.meta.setText("No clients yet - add one, or open the demo client to try everything safely.")

    def _client_changed(self, idx: int) -> None:
        root = self.client_box.itemData(idx)
        if not root:
            return
        self._root = root
        name = self.client_box.itemText(idx)
        if self.ctx.settings.get("active_client") != name:
            self.mgr.extensions.configure("accounting", {"active_client": name})
        self.refresh()

    # -- tabs ------------------------------------------------------------------------------------------
    def _build_overview(self) -> None:
        w = QWidget()
        g = QGridLayout(w)
        g.setSpacing(10)
        self.stats: dict[str, QLabel] = {}
        for i, (key, label) in enumerate((("exceptions", "Needs attention"), ("drafts", "Drafts to review"),
                                          ("unrec", "Unreconciled bank lines"), ("bank", "Bank balance"))):
            box = QVBoxLayout()
            big = QLabel("–")
            big.setObjectName("accBig")
            small = QLabel(label)
            small.setObjectName("accSmall")
            box.addWidget(big)
            box.addWidget(small)
            g.addLayout(box, 0, i)
            self.stats[key] = big
        g.addWidget(QLabel("Run now - each is a background agent; results come back as exceptions:"), 1, 0, 1, 4)
        for i, (label, template, wf) in enumerate(RUNS):
            b = QPushButton(label)
            b.setToolTip(workflows.BY_ID[wf].description)
            b.clicked.connect(lambda _=False, t=template, f=wf: self.run(t, f))
            g.addWidget(b, 2 + i // 3, i % 3)
        self.hint = QLabel("")
        self.hint.setObjectName("accSmall")
        self.hint.setWordWrap(True)
        g.addWidget(self.hint, 6, 0, 1, 4)
        g.setRowStretch(7, 1)
        self.tabs.addTab(w, "Overview")

    def _build_attention(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        self.attention = QTreeWidget()
        self.attention.setHeaderLabels(["Severity", "Needs attention", "About", "Suggested"])
        self.attention.setColumnWidth(0, 70)
        self.attention.setColumnWidth(1, 420)
        v.addWidget(self.attention, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        resolve = QPushButton("Mark resolved…")
        resolve.clicked.connect(self._resolve)
        row.addWidget(resolve)
        v.addLayout(row)
        self.tabs.addTab(w, "Needs attention")

    def _build_drafts(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        self.drafts = QTreeWidget()
        self.drafts.setHeaderLabels(["Draft", "Kind", "Status", "Confidence", "What"])
        self.drafts.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.drafts.setColumnWidth(4, 420)
        v.addWidget(self.drafts, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        for label, fn in (("Reject", self._reject), ("Post selected…", self._post),
                          ("Release payment batch…", self._release), ("Submit tax return…", self._submit)):
            b = QPushButton(label)
            b.clicked.connect(fn)
            row.addWidget(b)
        v.addLayout(row)
        self.tabs.addTab(w, "Drafts")

    def _build_parties(self) -> None:
        self.parties = QTreeWidget()
        self.parties.setHeaderLabels(["Type", "Party", "Invoice", "Due", "Days overdue", "Outstanding"])
        self.parties.setColumnWidth(1, 220)
        self.tabs.addTab(self.parties, "Debtors & creditors")

    def _build_deadlines(self) -> None:
        self.deadlines = QTreeWidget()
        self.deadlines.setHeaderLabels(["Due", "Obligation", "Days left", "Basis"])
        self.deadlines.setColumnWidth(1, 260)
        self.tabs.addTab(self.deadlines, "Deadlines")

    def _build_agents(self) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        self.agents = QTreeWidget()
        self.agents.setHeaderLabels(["Agent", "Trigger", "Next run", "Last run"])
        self.agents.setColumnWidth(0, 300)
        v.addWidget(self.agents, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("Remove agent")
        b.clicked.connect(self._remove_agent)
        row.addWidget(b)
        b = QPushButton("New agent…")
        b.clicked.connect(self._new_agent)
        row.addWidget(b)
        v.addLayout(row)
        self.tabs.addTab(w, "Agents")

    def _build_reports(self) -> None:
        self.reports = QListWidget()
        self.reports.itemDoubleClicked.connect(lambda it: os.startfile(it.data(Qt.ItemDataRole.UserRole)))
        self.tabs.addTab(self.reports, "Reports")

    # -- refresh ------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        if not self._root or not self.isVisible() and self.parent() is None:
            return
        try:
            s = self.session()
            ws = ClientWorkspace.load(self._root)
        except Exception as exc:  # noqa: BLE001
            self.meta.setText(f"Could not open this client: {exc}")
            return
        prof = ws.profile()
        self.meta.setText(f"Period {ws.period} · {prof.currency} · {prof.tax_label} {prof.tax_rate:g}% · "
                          f"{s.provider.display_name} · {prof.country}")
        me = ws.month_end or {}
        pct = int(round((me.get("status") or 0) * 100)) if me.get("period") == ws.period else 0
        self.me_bar.setValue(pct)
        self.me_label.setText(f"Month-end {ws.period}")
        xs = s.drafts.findings(status="open", limit=500)
        ds = s.drafts.list(status="draft,review", limit=500)
        self.stats["exceptions"].setText(str(len(xs)))
        self.stats["drafts"].setText(str(len(ds)))
        try:
            unrec = self._call(s.provider.bank_transactions(unreconciled_only=True))
            bal = self._call(s.provider.balance((ws.bank_accounts or ["1000"])[0], as_of=ws.period_end))
            self.stats["unrec"].setText(str(len(unrec)))
            self.stats["bank"].setText(prof.money(bal))
        except Exception:  # noqa: BLE001 - a file provider may not know
            self.stats["unrec"].setText("–")
            self.stats["bank"].setText("–")
        self.hint.setText(f"Client folder: {ws.root}\nDrop invoices, receipts and supplier statements into Inbox, "
                          "bank statements (CSV/XLSX) into Inbox\\Bank.")
        self._fill_attention(xs)
        self._fill_drafts(s.drafts.list(status="", limit=300))
        self._fill_parties(s, ws)
        self._fill_deadlines(ws)
        self._fill_agents()
        self._fill_reports(ws)

    def _fill_attention(self, xs: list[dict[str, Any]]) -> None:
        self.attention.clear()
        for x in xs:
            it = QTreeWidgetItem([x["severity"].upper(), x["title"], x["entity"], x["action"]])
            it.setForeground(0, SEVERITY_COLOUR.get(x["severity"], QColor(200, 200, 210)))
            it.setToolTip(1, x["detail"])
            it.setData(0, Qt.ItemDataRole.UserRole, x["id"])
            self.attention.addTopLevelItem(it)

    def _fill_drafts(self, ds: list[dict[str, Any]]) -> None:
        selected = {i.data(0, Qt.ItemDataRole.UserRole) for i in self.drafts.selectedItems()}
        self.drafts.clear()
        for d in ds:
            it = QTreeWidgetItem([d["id"], d["kind"], d["status"], f"{d['confidence']:.0%}", d["title"]])
            it.setData(0, Qt.ItemDataRole.UserRole, d["id"])
            it.setToolTip(4, d["reason"])
            self.drafts.addTopLevelItem(it)
            if d["id"] in selected:
                it.setSelected(True)

    def _fill_parties(self, s, ws: ClientWorkspace) -> None:
        self.parties.clear()
        try:
            rows = []
            for kind, label in (("sales", "Debtor"), ("purchase", "Creditor")):
                for i in self._call(s.provider.invoices(kind, "open")):
                    if i.outstanding > 0:
                        days = (date.fromisoformat(ws.period_end) - date.fromisoformat(i.due)).days
                        rows.append((label, i.party, i.number, i.due, max(0, days), ws.profile().money(i.outstanding)))
            for r in sorted(rows, key=lambda r: (r[0], -r[4])):
                it = QTreeWidgetItem([str(c) for c in r])
                if r[4] > 60:
                    it.setForeground(4, SEVERITY_COLOUR["high"])
                self.parties.addTopLevelItem(it)
        except Exception:  # noqa: BLE001
            log.debug("parties refresh failed", exc_info=True)

    def _fill_deadlines(self, ws: ClientWorkspace) -> None:
        self.deadlines.clear()
        for d in ws.profile().deadlines(date.today(), 120):
            left = (date.fromisoformat(d["due"]) - date.today()).days
            it = QTreeWidgetItem([d["due"], d["name"], str(left), d["basis"]])
            if left <= 14:
                it.setForeground(2, SEVERITY_COLOUR["review" if left >= 0 else "high"])
            self.deadlines.addTopLevelItem(it)

    def _fill_agents(self) -> None:
        self.agents.clear()
        sched = getattr(self.mgr, "scheduler", None)
        jobs = {j["id"]: j for j in (sched.jobs() if sched else [])}
        for a in self.ctx.settings.get("agents") or []:
            j = jobs.get(f"accounting:{a.get('id')}", {})
            last = j.get("last_fired") or 0
            it = QTreeWidgetItem([f"{a.get('name') or roles.BY_ID.get(a.get('template', ''), roles.TEMPLATES[0]).name}"
                                  f" - {a.get('client', '')}", j.get("trigger", "manual"), j.get("next", ""),
                                  date.fromtimestamp(last).isoformat() if last else "never"])
            it.setData(0, Qt.ItemDataRole.UserRole, a.get("id"))
            self.agents.addTopLevelItem(it)

    def _fill_reports(self, ws: ClientWorkspace) -> None:
        self.reports.clear()
        for sub in ("Reports", "Workpapers", "Audit", "Outbox", "Exports"):
            base = ws.path(sub)
            for dirpath, _dirs, files in os.walk(base):
                for f in sorted(files):
                    path = os.path.join(dirpath, f)
                    it = QListWidgetItem(os.path.relpath(path, ws.root))
                    it.setData(Qt.ItemDataRole.UserRole, path)
                    self.reports.addItem(it)

    # -- actions ------------------------------------------------------------------------------------------
    def run(self, template: str, workflow: str, extra: Optional[dict[str, Any]] = None) -> None:
        if not self._root:
            QMessageBox.information(self, "Accounting", "Pick or create a client first.")
            return
        task, spec = self.ext.task_for(template, self._root, workflow_id=workflow, extra=extra)
        self.mgr.submit_task(task, agent=spec)
        self.hint.setText(f"Started: {task.title}. Watch it in the orbs / Agents panel; exceptions land here.")

    def _selected_drafts(self) -> list[dict[str, Any]]:
        s = self.session()
        return [s.drafts.get(i.data(0, Qt.ItemDataRole.UserRole)) for i in self.drafts.selectedItems()] if s else []

    def _reject(self) -> None:
        s = self.session()
        for d in self._selected_drafts():
            if d["status"] in ("draft", "review", "approved"):
                s.drafts.set_status(d["id"], "rejected", reason="rejected in the dashboard")
        self.refresh()

    def _post(self) -> None:
        ids = [d["id"] for d in self._selected_drafts()
               if d["status"] in ("draft", "review", "approved")
               and d["kind"] in ("journal", "bill", "invoice", "bank_line", "match")]
        if not ids:
            QMessageBox.information(self, "Post drafts", "Select journals, bills, invoices or bank codings to post.")
            return
        self.run("bookkeeper", "post_drafts", {"draft_ids": ids})

    def _release(self) -> None:
        batch = next((d for d in self._selected_drafts() if d["kind"] == "payment_batch"), None)
        if batch is None:
            QMessageBox.information(self, "Release payments", "Select a payment batch draft.")
            return
        self.run("accounts_payable", "release_batch", {"draft_id": batch["id"]})

    def _submit(self) -> None:
        ret = next((d for d in self._selected_drafts() if d["kind"] == "tax_return"), None)
        if ret is None:
            QMessageBox.information(self, "Submit tax return", "Select a tax return draft.")
            return
        self.run("tax_preparation", "submit_return", {"draft_id": ret["id"]})

    def _resolve(self) -> None:
        items = self.attention.selectedItems()
        if not items:
            return
        note, ok = QInputDialog.getText(self, "Resolve", "Note (what was done):")
        if not ok:
            return
        s = self.session()
        for it in items:
            s.drafts.resolve_finding(it.data(0, Qt.ItemDataRole.UserRole), note)
        self.refresh()

    def _new_client(self) -> None:
        from .wizard import NewClientDialog
        dlg = NewClientDialog(self.ext, self.ctx, self)
        if dlg.exec() and dlg.created is not None:
            self.load_clients(select=dlg.created.client)

    def _demo_client(self) -> None:
        ws = self.ext.create_demo_client(self.ctx)
        self.load_clients(select=ws.client)

    def _new_agent(self) -> None:
        from .wizard import AgentSetupWizard
        dlg = AgentSetupWizard(self.mgr, self.ext, client=self.client_box.currentText(), parent=self)
        if dlg.exec():
            self.load_clients(select=dlg.client_name or self.client_box.currentText())
            self.tabs.setCurrentIndex(5)

    def _remove_agent(self) -> None:
        items = self.agents.selectedItems()
        if not items:
            return
        gone = {i.data(0, Qt.ItemDataRole.UserRole) for i in items}
        agents = [a for a in self.ctx.settings.get("agents") or [] if a.get("id") not in gone]
        self.mgr.extensions.configure("accounting", {"agents": agents})
        self.refresh()
