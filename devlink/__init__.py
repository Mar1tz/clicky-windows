"""HeyTora DevLink: developer tools read as data, not pixels.

The design ("HeyTora DevLink - Deep App Integration Design", 23 Oct 2026;
docs/devlink/DEVLINK.md) gives Tora structured access to the apps a
developer works in, starting with VS Code: which project and file they are
in, what is broken, what the last command printed - read through the
app's own API by a small connector, instead of guessed from a screenshot.
Screen vision stays for apps with no API; it stops being the first sense.

This package is the local agent inside HeyTora: the gateway connectors
reach (gateway), the Policy Engine (policy, redact), the Context Engine
(context), the Log Hub (logs), and the store they share (store). hub ties
them together; tools is what Tora's voice and agents call. The VS Code
connector is in connectors/vscode.

This is the design's phases 1 and 2: foundations, and read-only context.
Nothing here edits a file or runs a command.
"""
from __future__ import annotations

from typing import Optional

from .hub import Hub

_current: Optional[Hub] = None


def current() -> Optional[Hub]:
    """The running hub, or None when DevLink is off or has not started."""
    return _current


def set_current(hub: Optional[Hub]) -> None:
    global _current
    _current = hub


__all__ = ["Hub", "current", "set_current"]
