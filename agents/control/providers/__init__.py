"""Capability providers — one mechanism each, asked about any app.

See `agents/control/provider.py` for the protocol and the reasoning, and
`docs/agents/UNIVERSAL_CONTROL.md` for the measurements. Providers are
added one milestone at a time; `available()` is the single place the
registry looks, so a new one is a single line here.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("agents.control")

_MODULES: tuple[str, ...] = ("launcher",)    # a new provider is one line here


def available() -> list[Any]:
    """Providers that work on this machine, in preference order."""
    import importlib

    out: list[Any] = []
    for name in _MODULES:
        try:
            module = importlib.import_module(f"{__name__}.{name}")
            out.extend(module.providers())
        except Exception:  # noqa: BLE001 - a provider that cannot load is not fatal
            log.exception("capability provider %s could not be loaded", name)
    return out
