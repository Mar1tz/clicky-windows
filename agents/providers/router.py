"""Router — picks which provider answers a given kind of work.

The user may connect several AI accounts (Claude for coding, Gemini for
vision, a local model for cheap/fast). A task, or a single provider call,
carries a TaskKind; the router maps that to the best registered provider,
honouring an explicit per-agent hint and a hard vision requirement.

Selection order for `pick`:
  1. a `needs_vision` call drops every provider that can't see, up front;
  2. an explicit `hint` (an AgentSpec.provider_hint) wins if that provider
     survived the vision filter;
  3. otherwise the highest-priority provider registered for this exact kind;
  4. then the highest-priority provider registered for TaskKind.GENERAL;
  5. then any surviving provider (highest priority);
  6. nothing fits -> LookupError that names what was asked for and what exists,
     so a misconfiguration is legible instead of a mystery.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agents.core import (
    AgentProvider, ConnectedAccount, ProviderRouter, TaskKind,
)

from .accounts import AccountStore


@dataclass
class _Registration:
    provider: AgentProvider
    kinds: Optional[frozenset[TaskKind]]     # None = catch-all (any kind)
    priority: int
    order: int                               # registration order, tie-breaker


class Router(ProviderRouter):
    def __init__(self, accounts: Optional[AccountStore] = None) -> None:
        self._regs: list[_Registration] = []
        self._accounts = accounts
        self._seq = 0

    # -- registration ---------------------------------------------------------

    def register(
        self,
        provider: AgentProvider,
        *,
        kinds: Optional[list[TaskKind]] = None,
        priority: int = 0,
    ) -> None:
        """Register `provider` for `kinds` (None = every kind). Higher
        `priority` wins; registration order breaks ties."""
        self._regs.append(_Registration(
            provider=provider,
            kinds=frozenset(kinds) if kinds is not None else None,
            priority=priority,
            order=self._seq,
        ))
        self._seq += 1

    # -- selection ------------------------------------------------------------

    def pick(
        self,
        kind: TaskKind,
        *,
        hint: Optional[str] = None,
        needs_vision: bool = False,
    ) -> AgentProvider:
        pool = self._regs
        if needs_vision:
            pool = [r for r in pool if r.provider.capabilities().vision]
            if not pool:
                raise LookupError(
                    f"no vision-capable provider registered (kind={kind.value}, "
                    f"hint={hint!r}); registered: {self._names()}"
                )

        # 2. explicit hint
        if hint:
            hinted = [r for r in pool if r.provider.name == hint]
            if hinted:
                return self._best(hinted).provider

        # 3. exact kind, then 4. GENERAL, then 5. anything
        for want in (kind, TaskKind.GENERAL, None):
            matches = [r for r in pool if self._matches(r, want)]
            if matches:
                return self._best(matches).provider

        raise LookupError(
            f"no provider for kind={kind.value} (hint={hint!r}, "
            f"needs_vision={needs_vision}); registered: {self._names()}"
        )

    @staticmethod
    def _matches(reg: _Registration, want: Optional[TaskKind]) -> bool:
        if want is None:                     # step 5: any provider at all
            return True
        return reg.kinds is None or want in reg.kinds

    @staticmethod
    def _best(regs: list[_Registration]) -> _Registration:
        # Highest priority, earliest registration on a tie.
        return max(regs, key=lambda r: (r.priority, -r.order))

    # -- introspection --------------------------------------------------------

    def providers(self) -> list[AgentProvider]:
        seen: list[AgentProvider] = []
        for r in self._regs:
            if r.provider not in seen:
                seen.append(r.provider)
        return seen

    def accounts(self) -> list[ConnectedAccount]:
        return self._accounts.list() if self._accounts is not None else []

    def _names(self) -> str:
        names = [p.name for p in self.providers()]
        return ", ".join(names) if names else "(none)"
