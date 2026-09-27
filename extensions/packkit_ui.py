"""The dashboard every kit-built pack gets: a few numbers, up to three lists.

Drawn from the pack's snapshot(store), so a pack writes no Qt at all:

    {"title": "Sam's channel", "meta": "YouTube · 150 words a minute",
     "numbers": [("Ideas", "4"), ("Deals due", "1")],
     "lists": [("In production", [("Logo reveal - editing", "C:/.../logo.md")]), ...]}

Buttons run the pack's ACTIONS workflows as ordinary agent tasks, so an
action that changes something still goes through the approval dialog.
Double-clicking a row whose data is a file opens it. A module, not a
package: pack discovery skips it.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

log = logging.getLogger("extensions.packkit.ui")

QSS = """
QLabel#packTitle { font-size: 16px; font-weight: 600; color: rgb(240, 240, 245); }
QLabel#packMeta { font-size: 12px; color: rgb(170, 170, 190); }
QLabel#packBig { font-size: 22px; font-weight: 600; color: rgb(240, 240, 245); }
QLabel#packSmall { font-size: 11px; color: rgb(140, 140, 160); }
QListWidget { background-color: rgba(24, 24, 30, 230); color: rgb(225, 225, 235);
              border: 1px solid rgba(60, 60, 75, 180); border-radius: 8px; font-size: 12px; }
QListWidget::item { padding: 4px; }
"""


class PackDashboard(QWidget):
    MAX_LISTS = 3

    def __init__(self, manager, extension, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.mgr = manager
        self.ext = extension
        self.setStyleSheet(QSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        head = QHBoxLayout()
        self.title = QLabel(extension.manifest.name)
        self.title.setObjectName("packTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        for text, workflow in extension.ACTIONS:
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, w=workflow: self.run(w))
            head.addWidget(b)
        folder = QPushButton("Open folder")
        folder.clicked.connect(lambda: os.startfile(self.store.root))
        head.addWidget(folder)
        lay.addLayout(head)
        self.meta = QLabel("")
        self.meta.setObjectName("packMeta")
        lay.addWidget(self.meta)
        self._grid = QGridLayout()
        lay.addLayout(self._grid)
        self.numbers: dict[str, QLabel] = {}

        row = QHBoxLayout()
        self.lists: list[tuple[QLabel, QListWidget]] = []
        for _ in range(self.MAX_LISTS):
            col = QVBoxLayout()
            cap, box = QLabel(""), QListWidget()
            cap.setObjectName("packSmall")
            box.itemDoubleClicked.connect(self._open)
            col.addWidget(cap)
            col.addWidget(box, 1)
            row.addLayout(col, 1)
            self.lists.append((cap, box))
        lay.addLayout(row, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    @property
    def store(self):
        return self.ext.ensure_store(self.mgr.extensions._context(self.ext.id))

    def refresh(self) -> None:
        try:
            snap = self.ext.snapshot(self.store)
        except Exception:  # noqa: BLE001 - the pack's code; the next tick tries again
            log.debug("%s dashboard refresh failed", self.ext.id, exc_info=True)
            return
        self.title.setText(snap.get("title") or self.ext.manifest.name)
        self.meta.setText(snap.get("meta") or "")
        for col, (caption, text) in enumerate(snap.get("numbers") or []):
            if caption not in self.numbers:
                big, small = QLabel(), QLabel(caption)
                big.setObjectName("packBig")
                small.setObjectName("packSmall")
                self._grid.addWidget(big, 0, col)
                self._grid.addWidget(small, 1, col)
                self.numbers[caption] = big
            self.numbers[caption].setText(str(text))
        lists = list(snap.get("lists") or [])[:self.MAX_LISTS]
        for i, (cap, box) in enumerate(self.lists):
            caption, rows = lists[i] if i < len(lists) else ("", [])
            cap.setText(caption)
            box.setVisible(bool(caption))
            box.clear()
            for text, data in rows:
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, data)
                box.addItem(item)

    def run(self, workflow: str) -> None:
        task, spec = self.ext.task_for(workflow, self.store.root)
        self.mgr.submit_task(task, agent=spec)

    @staticmethod
    def _open(item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, str) and path and os.path.isfile(path):
            os.startfile(path)
