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
from .openai_compatible import OpenAICompatibleProvider, ProviderError
from .router import Router

__all__ = ["AccountStore", "GATEWAY_MODELS", "MockCall", "MockProvider",
           "OpenAICompatibleProvider", "ProviderError", "Router", "build_router",
           "provider_from_account", "provider_from_env"]

# Defaults for a gateway that fronts many vendors, ordered best-first.
# The provider falls through the chain when a route is benched, which on
# a real gateway is a matter of when rather than if.
#
# These were MEASURED, not assumed (2026-09-22): the obvious-looking
# "flash" routes were the worst picks. agy/gemini-3.6-flash-low timed out
# entirely, and agy/gemini-3.5-flash-low answers HTTP 200 with the text
# "Gemini 3.5 Flash is no longer available" — healthy-looking and
# useless. cc/claude-sonnet-4-6 answered in 0.5 s, so it leads the fast
# chain despite not being a small model. Re-measure before reordering.
GATEWAY_MODELS: dict[str, list[str]] = {
    "coding": ["claude/claude-sonnet-4-6", "cc/claude-sonnet-4-6",
               "agy/claude-sonnet-4-6"],
    "general": ["claude/claude-sonnet-4-6", "cc/claude-sonnet-4-6",
                "agy/claude-sonnet-4-6"],
    # Classification sits on the voice path: latency is the whole point.
    "fast": ["cc/claude-sonnet-4-6", "agy/gemini-3.1-flash-lite",
             "claude/claude-sonnet-4-6"],
    "vision": ["agy/gemini-3.6-flash-medium", "claude/claude-sonnet-4-6",
               "cc/claude-sonnet-4-6"],
}


def provider_from_account(account, key: Optional[str] = None) -> Optional[OpenAICompatibleProvider]:
    """Build a provider from a saved ConnectedAccount.

    Returns None when the account has no endpoint or is disabled, so a
    half-filled row in the settings UI is simply ignored rather than
    registering a provider that cannot work.
    """
    import os

    if not account.enabled or not (account.base_url or "").strip():
        return None
    chain = [m for m in (account.models or []) if m] or \
        ([account.default_model] if account.default_model else GATEWAY_MODELS["general"])
    return OpenAICompatibleProvider(
        base_url=account.base_url,
        api_key=key if key is not None else os.environ.get(account.env_key_name, ""),
        model=chain[0],
        fallback_models=chain[1:],
        name=account.provider or "custom",
    )


def provider_from_env(prefix: str = "OMNIROUTE") -> Optional[OpenAICompatibleProvider]:
    """Build the gateway provider from `TORA_<PREFIX>_{API_KEY,BASE_URL}`.

    Returns None when no base URL is configured, so the caller falls back
    to the mock and the app keeps working with no credentials at all.
    """
    import os

    base = os.environ.get(f"TORA_{prefix}_BASE_URL", "").strip()
    if not base:
        return None
    chain = GATEWAY_MODELS["general"]
    return OpenAICompatibleProvider(
        base_url=base,
        api_key=os.environ.get(f"TORA_{prefix}_API_KEY", "").strip(),
        model=chain[0],
        fallback_models=chain[1:],
        name=prefix.lower(),
    )


def build_router(
    accounts: Optional[AccountStore] = None,
    *,
    fallback: Optional[AgentProvider] = None,
    gateway: Optional[bool] = None,
) -> Router:
    """Assemble the provider router.

    `fallback` (default MockProvider) is registered as a catch-all at the
    lowest priority, so there is always something to answer any kind.

    `gateway` controls the OpenAI-compatible provider: None auto-detects
    from the environment, False never registers it. It is deliberately
    NOT inferred from `fallback` — passing a fallback used to suppress the
    gateway, which meant the app silently ran on the mock while looking
    like it was configured. Say what you mean instead.
    """
    router = Router(accounts=accounts)

    fb = fallback if fallback is not None else MockProvider()
    router.register(fb, kinds=None, priority=0)          # catch-all, lowest

    # ── OpenAI-compatible gateway ───────────────────────────────────────────
    # One adapter reaches Claude, Gemini, GPT and open weights, so this is
    # registered for every kind when a base URL is configured. Kinds differ
    # only in which model chain they prefer.
    if gateway is not False:
        # Saved accounts win over the bare environment: the settings page
        # is where a user expects to change this, and an account can pin
        # its own model chain and task kinds.
        registered_any = False
        for account in (accounts.list(enabled_only=True) if accounts else []):
            built = provider_from_account(account)
            if built is None:
                continue
            kinds = []
            for name in account.kinds or []:
                try:
                    kinds.append(TaskKind(name))
                except ValueError:
                    continue
            # A kind-scoped account is a routing rule ("coding goes here")
            # and must outrank the catch-all endpoint it refines; at equal
            # priority the router happily picks the general one.
            router.register(built, kinds=kinds or None, priority=50 if kinds else 40)
            registered_any = True

        remote = None if registered_any else provider_from_env()
        if remote is not None:
            router.register(remote, kinds=None, priority=20)
            for kind_name, chain in GATEWAY_MODELS.items():
                try:
                    kind = TaskKind(kind_name)
                except ValueError:
                    continue
                router.register(
                    OpenAICompatibleProvider(
                        base_url=remote.base_url, api_key=remote.api_key,
                        model=chain[0], fallback_models=chain[1:],
                        name=f"{remote.name}:{kind_name}",
                        vision=(kind_name == "vision"),
                        cost_tier=0 if kind_name == "fast" else 2,
                    ),
                    kinds=[kind], priority=30)

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
