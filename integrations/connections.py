"""The person's connected accounts (CI-12, CI-13, CI-14, CI-15).

Kept in connections.json in the data folder - provider, account, state,
granted scopes and the vault reference; the tokens themselves are only in
the vault. One account per provider and email; several are fine, and the
one used is always named.

    connect(provider, features)  the browser, then connected / limited / denied
    token(connection)            a fresh access token - an expired or revoked
                                 grant marks reauth_required instead (one
                                 reconnect, offered when the feature is used)
    ensure(connection, features) adds a scope only when its feature is used;
                                 a partial grant leaves just that part off
    disconnect(connection)       Google revokes it, the tokens are deleted;
                                 if Google does not confirm, the connection is
                                 left blocked and the person told where to
                                 remove it by hand
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from integrations import oauth, providers, vault
from integrations.lifecycle import Connection, State

MANAGE_URL = "https://myaccount.google.com/permissions"


class Connections:
    def __init__(self, path: Optional[Path] = None, *, transport=None, authorize=None) -> None:
        if path is None:
            from config import data_dir
            path = data_dir() / "connections.json"
        self.path = Path(path)
        self.transport = transport
        self._authorize = authorize or oauth.authorize
        self._lock = threading.Lock()
        self._items: dict[str, Connection] = {}
        self._load()

    # -- the list ------------------------------------------------------------------------

    def all(self, provider: str = "") -> list[Connection]:
        with self._lock:
            return [c for c in self._items.values() if not provider or c.provider == provider]

    def usable(self, provider: str, feature: str, account: str = "") -> list[Connection]:
        """Connections that can do `feature` now (account narrows it)."""
        scope = providers.scopes_for(provider, [feature])
        return [c for c in self.all(provider) if c.state.usable and set(scope) <= set(c.scopes)
                and (not account or c.account.lower() == account.lower())]

    # -- connecting -----------------------------------------------------------------------

    def connect(self, provider: str, features: list[str], *, login_hint: str = "") -> Connection:
        wanted = providers.scopes_for(provider, features)
        pending = Connection(provider, login_hint, State.NOT_CONNECTED, wanted=wanted)
        pending.move(State.AUTHORIZING)
        try:
            grant = self._authorize(wanted, login_hint=login_hint)
        except oauth.Refused as exc:
            pending.move(State.DENIED if exc.denied else State.ERROR, str(exc))
            return pending
        account = grant.account or login_hint or "account"
        with self._lock:
            conn = self._items.get(f"{provider}:{account.lower()}") or Connection(provider, account)
            conn.move(State.AUTHORIZING)            # from any state an authorizing can follow
            conn.account, conn.wanted = account, sorted(set(conn.wanted) | set(wanted))
            conn.scopes = sorted(set(conn.scopes) | set(grant.scopes))
            conn.vault_ref = vault.ref(provider, account)
            vault.vault().put(conn.vault_ref, grant.secret())
            missing = [s for s in wanted if s not in conn.scopes]
            conn.move(State.LIMITED if missing else State.CONNECTED,
                      "Some of what was asked for was not allowed." if missing else "")
            self._items[conn.key] = conn
            self._save()
        return conn

    def ensure(self, conn: Connection, features: list[str]) -> Connection:
        """CI-15: the scopes those features need, asked for now if missing."""
        need = [s for s in providers.scopes_for(conn.provider, features) if s not in conn.scopes]
        if not need:
            return conn
        return self.connect(conn.provider, features, login_hint=conn.account)

    # -- using ------------------------------------------------------------------------------

    def token(self, conn: Connection) -> Optional[str]:
        """A fresh access token, or None (and reauth_required) when the
        grant is gone. Never logged, never stored outside the vault."""
        if not conn.state.usable:
            return None
        secret = vault.vault().get(conn.vault_ref) or {}
        if secret.get("access_token") and secret.get("expires_at", 0) > time.time():
            return secret["access_token"]
        try:
            grant = oauth.refresh(secret.get("refresh_token", ""), transport=self.transport)
        except oauth.Refused as exc:
            self._mark(conn, State.REAUTH_REQUIRED, str(exc))
            return None
        vault.vault().put(conn.vault_ref, grant.secret())
        return grant.access_token

    def expired(self, conn: Connection, why: str = "") -> None:
        """A service refused the token (401): reconnect, once, when next used."""
        self._mark(conn, State.REAUTH_REQUIRED, why or "The connection was removed or has expired.")

    # -- disconnecting -------------------------------------------------------------------

    def disconnect(self, conn: Connection) -> bool:
        """True when Google confirmed and the tokens are gone. Otherwise the
        connection stays blocked (error) and its detail says where to remove
        it by hand."""
        self._mark(conn, State.REVOKING)
        secret = vault.vault().get(conn.vault_ref) or {}
        token = secret.get("refresh_token") or secret.get("access_token") or ""
        confirmed = oauth.revoke(token, transport=self.transport) if token else True
        if not confirmed:
            self._mark(conn, State.ERROR, f"Google did not confirm. Remove HeyTora's access at {MANAGE_URL}.")
            return False
        vault.vault().delete(conn.vault_ref)
        with self._lock:
            conn.move(State.DISCONNECTED)
            conn.scopes = []
            self._items.pop(conn.key, None)
            self._save()
        return True

    # -- disk ------------------------------------------------------------------------------

    def _mark(self, conn: Connection, state: State, detail: str = "") -> None:
        with self._lock:
            conn.move(state, detail)
            self._items[conn.key] = conn
            self._save()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for d in data.get("connections", []):
            c = Connection.from_dict(d)
            self._items[c.key] = c

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"connections": [c.to_dict() for c in self._items.values()]}, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)


_connections: Optional[Connections] = None


def connections() -> Connections:
    global _connections
    if _connections is None:
        _connections = Connections()
    return _connections
