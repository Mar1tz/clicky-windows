"""Connected accounts — which AI the workforce uses, and for what.

Spec §6/§7. Two things the user needs to control, and they are different
questions:

  Endpoints   where requests go: a gateway, a local server, or
              api.openai.com. Name, URL, key, on/off, and a Test button
              that actually calls the endpoint.
  Routing     which model chain each kind of task prefers — coding,
              fast, vision, general — as an ordered fallback list,
              because on any real endpoint routes go down.

Keys are never written to the accounts file. They go to the environment
and to `.env` through `config.set_agent_env`, which is where the rest of
Tora keeps secrets. The JSON holds only the endpoint, the chains and the
enabled flag, so it can be synced or inspected safely.
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from agents.core import ConnectedAccount
from ui import design

log = logging.getLogger("agents.ui.accounts")

ROUTING_KINDS = [
    ("coding", "Coding — writing and editing code"),
    ("general", "General — everything else"),
    ("fast", "Fast — classification, on the voice path"),
    ("vision", "Vision — screenshots and images"),
]

ACCOUNTS_QSS = design.PANEL_QSS + """
QFrame#acctCard {
    background-color: rgba(28, 28, 34, 210);
    border: 1px solid rgba(60, 60, 75, 150);
    border-radius: 10px;
}
QLabel#acctLabel { font-size: 12px; font-weight: 600; color: rgb(235, 235, 245); }
QLabel#acctHint  { font-size: 11px; color: rgb(140, 140, 160); }
QLabel#acctState { font-size: 11px; }
QLineEdit {
    background-color: rgba(40, 40, 50, 200);
    border: 1px solid rgba(90, 90, 110, 150);
    border-radius: 8px; color: rgb(235, 235, 245);
    font-size: 12px; padding: 5px 8px;
}
QCheckBox { font-size: 12px; color: rgb(228, 228, 238); }
QPushButton { font-size: 12px; padding: 5px 14px; border-radius: 8px;
              background-color: rgba(60, 60, 75, 110);
              border: 1px solid rgba(90, 90, 110, 150); color: rgb(230, 230, 240); }
QPushButton:hover { background-color: rgba(0, 120, 255, 70); }
QPushButton#acctSave { background-color: rgba(0, 120, 255, 120);
                       border-color: rgba(0, 120, 255, 200); font-weight: 600; }
QScrollArea > QWidget > QWidget { background: transparent; }
QWidget#acctHolder { background: transparent; }
"""


class EndpointCard(QFrame):
    """One OpenAI-compatible endpoint."""

    removed = pyqtSignal(object)        # self

    def __init__(self, account: ConnectedAccount, key: str = "",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("acctCard")
        self.account = account

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.enabled = QCheckBox(account.label or account.provider or "Endpoint")
        self.enabled.setChecked(account.enabled)
        self.state = QLabel("")
        self.state.setObjectName("acctState")
        top.addWidget(self.enabled)
        top.addStretch(1)
        top.addWidget(self.state)
        root.addLayout(top)

        self.name = QLineEdit(account.provider)
        self.name.setPlaceholderText("short name, e.g. omniroute  (becomes TORA_<NAME>_API_KEY)")
        root.addWidget(self.name)

        self.url = QLineEdit(account.base_url)
        self.url.setPlaceholderText("https://your-gateway.example/v1")
        root.addWidget(self.url)

        self.key = QLineEdit(key)
        self.key.setPlaceholderText("API key — stored in .env, never in the accounts file")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        root.addWidget(self.key)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.test_btn = QPushButton("Test")
        show = QPushButton("Show key")
        show.setCheckable(True)
        show.toggled.connect(
            lambda on: self.key.setEchoMode(QLineEdit.EchoMode.Normal if on
                                            else QLineEdit.EchoMode.Password))
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: self.removed.emit(self))
        buttons.addWidget(self.test_btn)
        buttons.addWidget(show)
        buttons.addStretch(1)
        buttons.addWidget(remove)
        root.addLayout(buttons)

    def to_account(self) -> ConnectedAccount:
        self.account.provider = (self.name.text().strip() or "custom").lower()
        self.account.label = self.account.label or self.account.provider
        self.account.base_url = self.url.text().strip()
        self.account.enabled = self.enabled.isChecked()
        return self.account

    def set_state(self, text: str, ok: Optional[bool] = None) -> None:
        colour = design.TEXT_SECONDARY if ok is None else (
            design.SUCCESS if ok else design.ERROR)
        self.state.setText(text)
        self.state.setStyleSheet(
            f"color: rgb({colour.red()}, {colour.green()}, {colour.blue()});")


class ConnectedAccountsDialog(QWidget):
    """Endpoints + routing, applied to the live manager on save."""

    saved = pyqtSignal()

    def __init__(self, manager=None, store=None, config=None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setStyleSheet(ACCOUNTS_QSS)
        self.setWindowTitle("Connected accounts")
        self.resize(620, 620)

        self._manager = manager
        if config is None:
            from config import cfg as config
        self._cfg = config
        if store is None:
            from agents.providers import AccountStore
            store = AccountStore()
        self._store = store
        self._cards: list[EndpointCard] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(8)

        title = QLabel("Connected accounts")
        title.setObjectName("title")
        root.addWidget(title)
        root.addWidget(self._hint(
            "Any OpenAI-compatible endpoint: a gateway fronting several vendors, "
            "a local model server, or api.openai.com."))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("acctHolder")
        scroll.viewport().setAutoFillBackground(False)
        self._list = QVBoxLayout(holder)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(8)
        self._list.addStretch(1)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        add = QPushButton("Add endpoint")
        add.clicked.connect(lambda: self.add_card(ConnectedAccount(provider="custom")))
        root.addWidget(add, alignment=Qt.AlignmentFlag.AlignLeft)

        root.addWidget(self._label("Routing — preferred models, best first"))
        root.addWidget(self._hint(
            "Comma-separated. The provider falls through the list when a route "
            "is down, which on a busy gateway happens regularly."))
        self.chains: dict[str, QLineEdit] = {}
        for kind, description in ROUTING_KINDS:
            row = QHBoxLayout()
            row.setSpacing(8)
            name = QLabel(kind)
            name.setObjectName("acctLabel")
            name.setFixedWidth(64)
            edit = QLineEdit(", ".join(self._chain_for(kind)))
            edit.setToolTip(description)
            self.chains[kind] = edit
            row.addWidget(name)
            row.addWidget(edit, 1)
            root.addLayout(row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        save = QPushButton("Save")
        save.setObjectName("acctSave")
        save.clicked.connect(self.save)
        buttons.addWidget(close)
        buttons.addWidget(save)
        root.addLayout(buttons)

        self._load_cards()

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("acctLabel")
        return lab

    @staticmethod
    def _hint(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("acctHint")
        lab.setWordWrap(True)
        return lab

    def _chain_for(self, kind: str) -> list[str]:
        from agents.providers import GATEWAY_MODELS
        for acc in self._store.list():
            if acc.kinds == [kind] and acc.models:
                return acc.models
        return GATEWAY_MODELS.get(kind, [])

    def _load_cards(self) -> None:
        accounts = [a for a in self._store.list() if not a.kinds]
        if not accounts:
            # Seed from the environment so an existing .env setup shows up
            # rather than presenting an empty page to someone already wired.
            import os
            base = os.environ.get("TORA_OMNIROUTE_BASE_URL", "")
            if base:
                accounts = [ConnectedAccount(provider="omniroute", label="OmniRoute",
                                             base_url=base)]
        for account in accounts:
            self.add_card(account)

    def add_card(self, account: ConnectedAccount) -> EndpointCard:
        import os
        card = EndpointCard(account, os.environ.get(account.env_key_name, ""))
        card.removed.connect(self._remove_card)
        card.test_btn.clicked.connect(lambda _=False, c=card: self.test(c))
        self._cards.append(card)
        self._list.insertWidget(self._list.count() - 1, card)
        return card

    def _remove_card(self, card: EndpointCard) -> None:
        self._cards.remove(card)
        self._store.remove(card.account.id)
        card.setParent(None)
        card.deleteLater()

    # -- actions -------------------------------------------------------------

    def test(self, card: EndpointCard) -> None:
        """Call the endpoint for real — a green tick that never made a
        request would be worse than no button."""
        from agents.providers import provider_from_account
        account = card.to_account()
        provider = provider_from_account(account, key=card.key.text().strip())
        if provider is None:
            card.set_state("no URL", False)
            return
        card.set_state("testing…")
        try:
            models = self._run(provider.list_models())
            card.set_state(f"OK — {len(models)} models", True)
        except Exception as exc:  # noqa: BLE001
            card.set_state(f"failed: {type(exc).__name__}", False)
            log.info("endpoint test failed for %s: %s", account.base_url, exc)
        finally:
            try:
                self._run(provider.aclose())
            except Exception:  # noqa: BLE001
                pass

    def _run(self, coro, timeout: float = 20.0):
        """Run a coroutine off the GUI thread, on the agent runtime when
        there is one."""
        if self._manager is not None:
            return self._manager.runtime.call(coro, timeout=timeout)
        import asyncio
        return asyncio.run(coro)

    def save(self) -> None:
        for card in self._cards:
            account = card.to_account()
            key = card.key.text().strip()
            self._cfg.set_agent_env(account.env_key_name, key)
            self._cfg.set_agent_env(account.env_url_name, account.base_url)
            self._store.add(account)

        for kind, edit in self.chains.items():
            models = [m.strip() for m in edit.text().split(",") if m.strip()]
            self.save_chain(self._store, kind, models)

        self.apply_to(self._manager, self._store)
        self.saved.emit()

    @staticmethod
    def save_chain(store, kind: str, models: list[str]) -> None:
        """Store a routing chain as a kind-scoped account row."""
        existing = next((a for a in store.list() if a.kinds == [kind]), None)
        if existing is None:
            existing = ConnectedAccount(provider=f"route-{kind}", label=f"routing: {kind}",
                                        kinds=[kind])
        existing.models = models
        parent = next((a for a in store.list() if not a.kinds and a.enabled), None)
        if parent is not None:
            existing.base_url = parent.base_url
            existing.provider = parent.provider
            existing.kinds = [kind]
        store.add(existing)

    @staticmethod
    def apply_to(manager, store) -> None:
        """Rebuild the router so changes take effect without a restart."""
        if manager is None:
            return
        try:
            from agents.providers import build_router
            manager.router = build_router(store)
        except Exception:  # noqa: BLE001
            log.exception("rebuilding the router after a settings change failed")
