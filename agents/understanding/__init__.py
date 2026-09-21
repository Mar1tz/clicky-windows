"""Understanding layer: turn a user utterance into an Intent (chat vs task).

`HeuristicIntentClassifier` is the always-on default; `ProviderIntentClassifier`
upgrades to an LLM classification when a provider is available and falls back
to the heuristic on any failure. `TieredIntentClassifier` combines them - rules
when they are confident, the model only for the ambiguous middle - which keeps
the voice path fast.
"""
from __future__ import annotations

from .classifier import (
    HeuristicIntentClassifier, ProviderIntentClassifier, TieredIntentClassifier,
)

__all__ = ["HeuristicIntentClassifier", "ProviderIntentClassifier",
           "TieredIntentClassifier"]
