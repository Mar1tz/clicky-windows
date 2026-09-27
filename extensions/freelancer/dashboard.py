"""The Freelancer dashboard module: the business on one screen.

    Header    name · currency · Weekly admin · Year-end pack · Open folder
    Numbers   unpaid · overdue · unbilled · set aside this tax year
    Lists     invoice drafts (Issue goes through the approval dialog - the
              dashboard never puts anything on the books itself) · who is
              late · the Outbox, where double-clicking an email opens it
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from extensions.accounting.books import to_minor
from extensions.accounting.ui.dashboard import QSS

log = logging.getLogger("extensions.freelancer.ui")


class FreelancerDashboard(QWidget):
    def __init__(self, manager, extension, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.mgr = manager
        self.ext = extension
        self.setStyleSheet(QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        self.title = QLabel("Freelancer")
        self.title.setObjectName("accTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        for text, workflow in (("Weekly admin", "weekly_admin"), ("Year-end pack", "year_end")):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, w=workflow: self.run(w))
            head.addWidget(b)
        folder = QPushButton("Open folder")
        folder.clicked.connect(lambda: os.startfile(self.business.root))
        head.addWidget(folder)
        lay.addLayout(head)
        self.meta = QLabel("")
        self.meta.setObjectName("accMeta")
        lay.addWidget(self.meta)

        grid = QGridLayout()
        self.numbers: dict[str, QLabel] = {}
        for col, (key, caption) in enumerate((("unpaid", "Unpaid"), ("overdue", "Overdue"),
                                              ("unbilled", "Unbilled"), ("set_aside", "Set aside for tax"))):
            big, small = QLabel("-"), QLabel(caption)
            big.setObjectName("accBig")
            small.setObjectName("accSmall")
            grid.addWidget(big, 0, col)
            grid.addWidget(small, 1, col)
            self.numbers[key] = big
        lay.addLayout(grid)

        row = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(self._caption("Invoice drafts"))
        self.drafts = QListWidget()
        left.addWidget(self.drafts, 1)
        issue = QPushButton("Issue selected…")
        issue.setToolTip("Puts it on the books and writes the email - you approve it first")
        issue.clicked.connect(self._issue)
        left.addWidget(issue)
        row.addLayout(left, 1)
        mid = QVBoxLayout()
        mid.addWidget(self._caption("Late"))
        self.late = QListWidget()
        mid.addWidget(self.late, 1)
        row.addLayout(mid, 1)
        right = QVBoxLayout()
        right.addWidget(self._caption("Outbox - yours to send"))
        self.outbox = QListWidget()
        self.outbox.itemDoubleClicked.connect(self._open_email)
        right.addWidget(self.outbox, 1)
        row.addLayout(right, 1)
        lay.addLayout(row, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    @staticmethod
    def _caption(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("accSmall")
        return lbl

    @property
    def business(self):
        return self.ext.ensure_business(self.mgr.extensions._context("freelancer"))

    def refresh(self) -> None:
        try:
            b = self.business
            o = b.overview()
        except Exception:  # noqa: BLE001 - the books may be mid-write; next tick
            log.debug("freelancer dashboard refresh failed", exc_info=True)
            return
        m = lambda v: b.money(to_minor(v))  # noqa: E731
        self.title.setText(o["business"])
        self.meta.setText(f"{o['currency']} · rate {b.money(b.rate)}/h · terms {b.terms_days} days · "
                          f"tax year {o['tax_year'][0]} to {o['tax_year'][1]}")
        self.numbers["unpaid"].setText(f"{m(o['unpaid'])}  ({o['unpaid_count']})")
        self.numbers["overdue"].setText(f"{m(o['overdue'])}  ({o['overdue_count']})")
        self.numbers["unbilled"].setText(f"{o['unbilled_hours']:g} h  {m(o['unbilled_value'])}")
        self.numbers["set_aside"].setText(f"{m(o['set_aside'])}  ({o['set_aside_pct']:g}%)")
        self._fill(self.drafts, [(f"#{d['id']} {d['client']} {m(d['total'])}", d["id"]) for d in b.drafts()])
        self._fill(self.late, [(f"{i['number']} {i['party']} {m(i['outstanding'])} · {i['days_late']} days", "")
                               for i in b.overdue()])
        self._fill(self.outbox, [(f"{e['created'][:10]} {e['subject']}", e["path"]) for e in b.outbox()[:50]])

    @staticmethod
    def _fill(box: QListWidget, rows: list[tuple[str, object]]) -> None:
        box.clear()
        for text, data in rows:
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, data)
            box.addItem(item)

    def run(self, workflow: str, extra: Optional[dict] = None) -> None:
        task, spec = self.ext.task_for(workflow, self.business.root, extra)
        self.mgr.submit_task(task, agent=spec)

    def _issue(self) -> None:
        item = self.drafts.currentItem()
        if item is not None:
            self.run("issue_invoice", {"draft_id": item.data(Qt.ItemDataRole.UserRole)})

    @staticmethod
    def _open_email(item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and os.path.isfile(path):
            os.startfile(path)
