"""Provider layer: the tool-calling LLMs and the router that chooses between
them.

`build_router` is the one entry point the manager calls. Phase 1 wires only
the MockProvider as a catch-all so the whole runtime is exercisable with no
network. Phase 2 registers real adapters (each a separate `agents/providers/
<name>.py`) at higher priority for the kinds they are best at, driven by the
user's ConnectedAccounts — the marked hooks below are where they slot in.
"""
from __future__ import annotations

from typing import Optional

from agents.core import AgentProvider, TaskKind

from .accounts import AccountStore
from .mock import MockCall, MockProvider
from .router import Router

__all__ = ["AccountStore", "MockCall", "MockProvider", "Router", "build_router"]


def build_router(
    accounts: Optional[AccountStore] = None,
    *,
    fallback: Optional[AgentProvider] = None,
) -> Router:
    """Assemble the provider router.

    `fallback` (default MockProvider) is registered as a catch-all at the
    lowest priority, so there is always something to answer any kind. Real
    providers register above it in Phase 2.
    """
    router = Router(accounts=accounts)

    fb = fallback if fallback is not None else MockProvider()
    router.register(fb, kinds=None, priority=0)          # catch-all, lowest

    # ── Phase 2 hooks — register real adapters above the fallback ────────────
    # Each reads its key via accounts.api_key_for(...) and is skipped when the
    # user has not connected it, so this stays a no-op until Phase 2 lands.
    #
    #   if accounts and _enabled(accounts, "gemini"):
    #       from .gemini import GeminiAgentProvider
    #       router.register(GeminiAgentProvider(accounts),
    #                       kinds=[TaskKind.VISION, TaskKind.GENERAL], priority=20)
    #   if accounts and _enabled(accounts, "claude"):
    #       from .claude import ClaudeAgentProvider
    #       router.register(ClaudeAgentProvider(accounts),
    #                       kinds=[TaskKind.CODING], priority=30)
    #   if accounts and _enabled(accounts, "openai"):
    #       from .openai import OpenAIAgentProvider
    #       router.register(OpenAIAgentProvider(accounts), priority=10)
    #   from .local import LocalProvider           # always available, cheap/fast
    #   router.register(LocalProvider(), kinds=[TaskKind.FAST], priority=5)
    # ─────────────────────────────────────────────────────────────────────────

    return router
