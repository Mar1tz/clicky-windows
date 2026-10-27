"""What a model call costs, in dollars and in the credits users see (OB-01).

Prices are list prices per million tokens (input, output), the same ones
the app already uses (ai/gemini_vision.PRICES; docs/COSTS.md for Claude).
The server's table (supabase/functions/heytora/rules.ts) is the one that
bills; this one shows a job's running cost on the PC and lets a job be
capped in money. A model it does not know is priced at the dearest
family it could be - a cost shown too high is a surprise nobody minds.

1 credit = 1 US cent of what Tora costs (docs/COSTS.md).
"""
from __future__ import annotations

# family (matched in the model's name, first match wins) -> USD per 1M (input, output)
PRICES: tuple[tuple[str, tuple[float, float]], ...] = (
    ("flash-lite", (0.15, 0.6)),
    ("flash", (0.5, 3.0)),
    ("pro", (2.5, 15.0)),
    ("haiku", (1.0, 5.0)),
    ("sonnet", (3.0, 15.0)),
    ("opus", (15.0, 75.0)),
    ("gemini", (2.5, 15.0)),               # a Gemini model of no known family: priced as pro
    ("claude", (3.0, 15.0)),               # the agents' default route (OmniRoute, Sonnet)
    ("mock", (0.0, 0.0)),
)
UNKNOWN = (15.0, 75.0)


def rates(model: str) -> tuple[float, float]:
    name = (model or "").lower()
    for family, rate in PRICES:
        if family in name:
            return rate
    return UNKNOWN if name else (0.0, 0.0)


def price_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = rates(model)
    return (max(0, prompt_tokens) * pin + max(0, completion_tokens) * pout) / 1_000_000


def credits(usd: float) -> float:
    return round(usd * 100, 2)
