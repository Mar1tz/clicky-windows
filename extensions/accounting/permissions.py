"""Accounting action levels and confidence-based automation.

Accounting needs stricter permissions than ordinary automation, and the
strictness must live in the tools, not in a prompt a model can talk
itself out of. Every accounting tool declares one level:

    READ       look at the books                       -> RiskLevel.SAFE, never asks
    DRAFT      prepare something nobody has accepted   -> RiskLevel.LOW, runs within policy
    POST       change the books                        -> RiskLevel.HIGH + always_ask
    SENSITIVE  money leaves, or a filing is submitted  -> HIGH + always_ask + sensitive:
               no per-task grant, no voice "yes", the
               accountant types a confirm word

Default rule: financially consequential actions require explicit approval.

Confidence routing (ConfidencePolicy) decides what a DRAFT tool does with
the model's own certainty: at or above `auto` it drafts; between `flag`
and `auto` it drafts AND raises a finding for review; below `flag` it
does not act and asks the accountant. Thresholds are per client, with
the pack's settings as the default.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from agents.core import RiskLevel


class ActionLevel(str, Enum):
    READ = "read"
    DRAFT = "draft"
    POST = "post"
    SENSITIVE = "sensitive"

    @property
    def risk(self) -> RiskLevel:
        return {ActionLevel.READ: RiskLevel.SAFE, ActionLevel.DRAFT: RiskLevel.LOW,
                ActionLevel.POST: RiskLevel.HIGH, ActionLevel.SENSITIVE: RiskLevel.HIGH}[self]

    @property
    def always_ask(self) -> bool:
        return self in (ActionLevel.POST, ActionLevel.SENSITIVE)

    @property
    def sensitive(self) -> bool:
        return self is ActionLevel.SENSITIVE


LEVEL_RISK = {lvl.value: lvl.risk for lvl in ActionLevel}

# The confirm words a person types for each sensitive action.
CONFIRM_WORDS = {"release_payment_batch": "RELEASE", "submit_tax_return": "SUBMIT"}


@dataclass
class ConfidencePolicy:
    auto: float = 0.95
    flag: float = 0.70

    def __post_init__(self) -> None:
        self.auto = _clamp(self.auto)
        self.flag = min(_clamp(self.flag), self.auto)

    def route(self, confidence: Any) -> str:
        """'draft' | 'review' | 'ask'."""
        c = _clamp(confidence)
        if c >= self.auto:
            return "draft"
        if c >= self.flag:
            return "review"
        return "ask"

    def describe(self) -> str:
        return (f"Confidence >= {self.auto:.0%}: create the draft. {self.flag:.0%}-{self.auto:.0%}: "
                f"create the draft and flag it for review. Below {self.flag:.0%}: do not act - "
                "record an exception asking the accountant.")

    @classmethod
    def from_settings(cls, data: dict[str, Any] | None) -> "ConfidencePolicy":
        data = data or {}
        return cls(auto=float(data.get("auto", data.get("confidence_auto", 0.95))),
                   flag=float(data.get("flag", data.get("confidence_flag", 0.70))))


def _clamp(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v > 1.0:                        # "97" means 97%
        v = v / 100.0
    return max(0.0, min(1.0, v))
