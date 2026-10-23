"""Who may see what: the Policy Engine.

Nothing is read until it is granted, per workspace and per capability
(protocol.READ_CAPABILITIES). A grant lasts one answer ("once"), until
HeyTora restarts ("session") or for good ("always" - the only scope
written to disk). Pausing stops everything at once without forgetting a
grant; "local only" keeps reading but sends the model nothing.

Every event the gateway takes in and every answer a tool gives passes
check() first. The connector filters too, so it does not even send what
was not granted - but the connector is somebody else's process, and this
is the copy of the rule that counts.
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

from . import protocol as P
from .store import Store

REDACTED, LOCAL_ONLY = "redacted", "local_only"
DATA_POLICIES = (REDACTED, LOCAL_ONLY)


def workspace_id(folders: list[str], name: str = "") -> str:
    """A workspace's stable id: the same folders are the same workspace in
    every window and every run."""
    keys = sorted(os.path.normcase(os.path.normpath(f)) for f in folders if f) or [name or "untitled"]
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()[:16]


class Policy:
    def __init__(self, store: Store):
        self.store = store
        self._grants: dict[str, dict[str, str]] = store.saved_grants()     # always ones, then session/once
        self.paused = store.setting("paused") == "1"
        self.data_policy = store.setting("data_policy", REDACTED)
        if self.data_policy not in DATA_POLICIES:
            self.data_policy = REDACTED

    # ── grants ─────────────────────────────────────────────────────────────

    def grants(self, workspace: str) -> dict[str, str]:
        return dict(self._grants.get(workspace, {}))

    def grant(self, workspace: str, capability: str, scope: Optional[str]) -> None:
        """Grant `capability` for `scope`, or (scope None) take it back."""
        if capability not in P.READ_CAPABILITIES:
            raise ValueError(f"{capability} cannot be granted yet")
        if scope is not None and scope not in P.SCOPES:
            raise ValueError(f"unknown scope {scope!r}")
        held = self._grants.setdefault(workspace, {})
        if scope is None:
            held.pop(capability, None)
        else:
            held[capability] = scope
        self.store.save_grant(workspace, capability, scope if scope == P.ALWAYS else None)

    def revoke_all(self, workspace: str) -> None:
        for capability in list(self._grants.get(workspace, {})):
            self.grant(workspace, capability, None)

    def allows(self, workspace: str, capability: Optional[str]) -> bool:
        """May `capability` be used in `workspace` now? (Ingest: an event
        with no capability - a focus change - is always let in.)"""
        if self.paused:
            return False
        if capability is None:
            return True
        return capability in self._grants.get(workspace, {})

    def use(self, workspace: str, capability: str) -> bool:
        """allows(), and a "once" grant is spent by the answer it allows."""
        if not self.allows(workspace, capability):
            return False
        if self._grants[workspace].get(capability) == P.ONCE:
            self._grants[workspace].pop(capability)
        return True

    # ── switches ───────────────────────────────────────────────────────────

    def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)
        self.store.set_setting("paused", "1" if paused else "0")

    def set_data_policy(self, policy: str) -> None:
        if policy not in DATA_POLICIES:
            raise ValueError(f"unknown data policy {policy!r}")
        self.data_policy = policy
        self.store.set_setting("data_policy", policy)

    @property
    def sends(self) -> bool:
        """May anything read be sent to the model at all?"""
        return not self.paused and self.data_policy != LOCAL_ONLY
