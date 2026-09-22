"""ClientSession: a client's workspace, books and drafts, opened once.

Tools are handed only `ctx.workspace`. The session cache turns that path
into the open provider and draft store for the client, so fifty tool
calls in a month-end share one sqlite connection instead of opening
fifty.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Optional

from .drafts import DraftStore
from .providers import IAccountingProvider, make_provider
from .workspace import ClientWorkspace, workspace_for_path


class NotAClient(LookupError):
    pass


@dataclass
class ClientSession:
    ws: ClientWorkspace
    provider: IAccountingProvider
    drafts: DraftStore

    def close(self) -> None:
        try:
            self.provider.close()
        finally:
            self.drafts.close()

    def reload(self) -> None:
        """Pick up accounting.json edits (period change, thresholds)."""
        self.ws = ClientWorkspace.load(self.ws.root)


class SessionCache:
    def __init__(self) -> None:
        self._sessions: dict[str, ClientSession] = {}
        self._lock = threading.RLock()

    def get(self, path: str) -> ClientSession:
        ws = workspace_for_path(path)
        if ws is None:
            raise NotAClient(f"{path} is not an accounting client folder (no accounting.json). "
                             "Set the client up first, or run the task in the client's folder.")
        key = os.path.normcase(os.path.abspath(ws.root))
        with self._lock:
            s = self._sessions.get(key)
            if s is None:
                s = ClientSession(ws, make_provider(ws), DraftStore(os.path.join(ws.data, "drafts.sqlite")))
                self._sessions[key] = s
            else:
                s.reload()
            return s

    def drop(self, path: Optional[str] = None) -> None:
        with self._lock:
            keys = list(self._sessions) if path is None else \
                [os.path.normcase(os.path.abspath(path))]
            for k in keys:
                s = self._sessions.pop(k, None)
                if s is not None:
                    s.close()

    def open_roots(self) -> list[str]:
        with self._lock:
            return [s.ws.root for s in self._sessions.values()]
