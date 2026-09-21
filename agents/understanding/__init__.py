"""Understanding layer: turn a user utterance into an Intent (chat vs task).

`HeuristicIntentClassifier` is the always-on default; `ProviderIntentClassifier`
upgrades to an LLM classification when a provider is available and falls back
to the heuristic on any failure.
"""
from __future__ import annotations

from .classifier import HeuristicIntentClassifier, ProviderIntentClassifier

__all__ = ["HeuristicIntentClassifier", "ProviderIntentClassifier"]
