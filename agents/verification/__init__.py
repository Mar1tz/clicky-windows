"""Verification: did the agent really do what it said?

`RuleVerifier` checks tool effects against the disk — cheap, certain,
offline. `ProviderVerifier` judges the things rules cannot, such as
whether a written file actually contains what the step asked for.
`CompositeVerifier` combines them: rules first, the model where rules
are silent and on the final verdict.
"""
from .provider_verifier import CompositeVerifier, ProviderVerifier
from .verifier import RuleVerifier

__all__ = ["CompositeVerifier", "ProviderVerifier", "RuleVerifier"]
