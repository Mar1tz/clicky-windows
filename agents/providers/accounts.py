"""AccountStore — the user's connected AI accounts, persisted without secrets.

What the user picked in Settings -> Connected Accounts lives here as a small
JSON file: which providers they connected, the label, whether each is
enabled, the default model. **No API key is ever written to that file.** The
key is read at call time from the environment (`TORA_<PROVIDER>_API_KEY`), or,
for Gemini, from the existing `config` module so a user who already set up
Tora does not have to re-enter anything.

Keeping secrets out of the JSON means the file can be backed up, synced or
inspected without leaking credentials, and it matches how the rest of Tora
treats keys (env / config, never on disk in the profile).
"""
from __future__ import annotations

import json
import os
from typing import Optional

from agents.core import ConnectedAccount


def _default_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "HeyTora", "agent_accounts.json")


class AccountStore:
    """CRUD over ConnectedAccounts with JSON persistence. Load is lazy and
    tolerant: a missing or corrupt file yields an empty store rather than an
    error, because a broken accounts file must never stop the app booting."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or _default_path()
        self._accounts: list[ConnectedAccount] = []
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            self._accounts = []
            return
        self._accounts = [ConnectedAccount.from_dict(d) for d in raw.get("accounts", [])]

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {"accounts": [a.to_dict() for a in self._accounts]}
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, self.path)          # atomic on Windows and POSIX

    # -- CRUD -----------------------------------------------------------------

    def list(self, *, enabled_only: bool = False) -> list[ConnectedAccount]:
        if enabled_only:
            return [a for a in self._accounts if a.enabled]
        return list(self._accounts)

    def get(self, account_id: str) -> Optional[ConnectedAccount]:
        return next((a for a in self._accounts if a.id == account_id), None)

    def add(self, account: ConnectedAccount) -> ConnectedAccount:
        """Add, or replace an existing account with the same id."""
        self._accounts = [a for a in self._accounts if a.id != account.id]
        self._accounts.append(account)
        self._save()
        return account

    def remove(self, account_id: str) -> bool:
        before = len(self._accounts)
        self._accounts = [a for a in self._accounts if a.id != account_id]
        changed = len(self._accounts) != before
        if changed:
            self._save()
        return changed

    def enable(self, account_id: str, enabled: bool = True) -> bool:
        acc = self.get(account_id)
        if acc is None:
            return False
        acc.enabled = enabled
        self._save()
        return True

    def providers(self) -> list[str]:
        """Distinct provider names among enabled accounts, in first-seen order."""
        seen: list[str] = []
        for a in self._accounts:
            if a.enabled and a.provider not in seen:
                seen.append(a.provider)
        return seen

    # -- secrets (read-only, never persisted) ---------------------------------

    def api_key_for(self, account: ConnectedAccount) -> Optional[str]:
        """The API key for `account`, resolved at call time.

        Order: `TORA_<PROVIDER>_API_KEY` from the environment, then — for
        Gemini only — the key the rest of Tora already uses (config module).
        Returns None when nothing is configured; the caller decides whether
        that account is usable.
        """
        env_name = f"TORA_{account.provider.upper()}_API_KEY"
        key = os.environ.get(env_name)
        if key:
            return key
        if account.provider == "gemini":
            try:
                from config import cfg
                return cfg.google_api_key or None
            except Exception:
                return None
        return None
