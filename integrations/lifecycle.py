"""How a connection lives (CI-06, CI-12, CI-14).

    not_connected -> authorizing -> connected | limited | denied | error
    connected / limited -> reauth_required (expired or revoked) -> authorizing
    any -> revoking -> disconnected (or error, when revocation is unconfirmed)

One rule (can_become) decides every change, as TaskStatus does for jobs.
A connection is one account at one provider, keyed by the provider's own
account id (an email address for Google) - several may exist, and which one
sends is always shown, never switched silently.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum


class State(str, Enum):
    NOT_CONNECTED = "not_connected"
    AUTHORIZING = "authorizing"
    CONNECTED = "connected"
    LIMITED = "limited"                    # some asked-for scopes were not granted
    DENIED = "denied"                      # the person said no at the consent screen
    REAUTH_REQUIRED = "reauth_required"    # expired or revoked: one reconnect, when used
    ERROR = "error"
    REVOKING = "revoking"
    DISCONNECTED = "disconnected"

    @property
    def usable(self) -> bool:
        return self in (State.CONNECTED, State.LIMITED)


_NEXT = {
    State.NOT_CONNECTED: {State.AUTHORIZING},
    State.AUTHORIZING: {State.CONNECTED, State.LIMITED, State.DENIED, State.ERROR, State.NOT_CONNECTED},
    State.CONNECTED: {State.REAUTH_REQUIRED, State.REVOKING, State.LIMITED, State.ERROR, State.AUTHORIZING},
    State.LIMITED: {State.CONNECTED, State.REAUTH_REQUIRED, State.REVOKING, State.ERROR, State.AUTHORIZING},
    State.DENIED: {State.AUTHORIZING, State.NOT_CONNECTED},
    State.REAUTH_REQUIRED: {State.AUTHORIZING, State.REVOKING},
    State.ERROR: {State.AUTHORIZING, State.REVOKING, State.NOT_CONNECTED},
    State.REVOKING: {State.DISCONNECTED, State.ERROR},
    State.DISCONNECTED: {State.AUTHORIZING},
}


def can_become(old: State, new: State) -> bool:
    return new is old or new in _NEXT[old]


@dataclass
class Connection:
    provider: str
    account: str = ""                      # the provider's own id for the account (an email)
    state: State = State.NOT_CONNECTED
    scopes: list = field(default_factory=list)      # granted
    wanted: list = field(default_factory=list)      # asked for
    vault_ref: str = ""                    # where the tokens are (vault.py) - never the tokens
    detail: str = ""                       # the last error or why it is limited, in plain words
    updated_at: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.account.lower()}"

    def move(self, new: State, detail: str = "") -> bool:
        if not can_become(self.state, new):
            return False
        self.state, self.detail, self.updated_at = new, detail, time.time()
        return True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Connection":
        d = dict(d)
        d["state"] = State(d.get("state", "not_connected"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
