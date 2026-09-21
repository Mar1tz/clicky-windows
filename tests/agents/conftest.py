"""Keep the agent suite offline unless a test says otherwise.

`build_router` auto-detects a gateway from `TORA_OMNIROUTE_BASE_URL`, and
a developer with real credentials in `.env` would otherwise have the
whole suite quietly making paid network calls — slow, flaky, and
expensive. Clearing the variables here makes "no network" the default;
the provider tests set them explicitly on the cases that need them.
"""
from __future__ import annotations

import pytest

GATEWAY_VARS = ("TORA_OMNIROUTE_BASE_URL", "TORA_OMNIROUTE_API_KEY")


@pytest.fixture(autouse=True)
def _offline_by_default(monkeypatch, request):
    """Remove gateway credentials from the environment for every test.

    Opt back in with `@pytest.mark.live`, which is deselected by default:
        python -m pytest tests/agents -m live
    """
    if request.node.get_closest_marker("live"):
        return
    for name in GATEWAY_VARS:
        monkeypatch.delenv(name, raising=False)


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits a real provider over the network")


def pytest_collection_modifyitems(config, items):
    """Skip live tests unless -m live was asked for."""
    if "live" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="needs a real gateway; run with -m live")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)
