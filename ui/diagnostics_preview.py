"""See the diagnostics package before saving it (EN-16).

The redacted report (diagnostics.py), read-only, with Save - which writes
it to the data folder and opens it - Copy, and Close. Nothing is sent.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout

from ui.app_widgets import button


class DiagnosticsPreview(QDialog):
    def __init__(self, text: str, save: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("HeyTora diagnostics")
        self.resize(820, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lede = QLabel("This is everything the report holds. Keys, your user name and email addresses are "
                      "taken out. Nothing is sent - save it, then share it only if you choose to.")
        lede.setWordWrap(True)
        lay.addWidget(lede)
        self.view = QPlainTextEdit(text)
        self.view.setReadOnly(True)
        self.view.setFont(QFont("Consolas", 9))
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        copy = button("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(text))
        row.addWidget(copy)
        row.addStretch(1)
        close = button("Close")
        close.clicked.connect(self.reject)
        row.addWidget(close)
        keep = button("Save", "primary")
        keep.clicked.connect(lambda: (save(text), self.accept()))
        row.addWidget(keep)
        lay.addLayout(row)
