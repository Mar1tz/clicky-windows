"""Keep the agent suite offline unless a test says otherwise.

`build_router` auto-detects a gateway from `TORA_OMNIROUTE_BASE_URL`, and
a developer with real credentials in `.env` would otherwise have the
whole suite quietly making paid network calls — slow, flaky, and
expensive. Clearing the variables here makes "no network" the default;
the provider tests set them explicitly on the cases that need them.
"""
from __future__ import annotations

import pytest

GATEWAY_VARS = ("TORA_OMNIROUTE_BASE_URL", "TORA_OMNIROUTE_API_KEY",
                "TORA_GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY")


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
    # config.cfg caches the Google key at import; blank it too, or the
    # Gemini provider registers itself from there and tests go online.
    try:
        import config
        monkeypatch.setattr(config.cfg, "google_api_key", None, raising=False)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _grants_stay_in_the_sandbox(tmp_path, monkeypatch):
    """Standing desktop permissions are written to disk (DC-16), and the
    default path is the USER'S profile. The first test to call
    allow_desktop_action wrote a real "always allow Spotify next" into the
    Tora folder under LOCALAPPDATA - a permission nobody had granted, which
    then leaked into the next test. Every test gets its own file."""
    monkeypatch.setenv("TORA_GRANTS_FILE", str(tmp_path / "desktop_grants.json"))
    # Same rule, same reason: the launcher caches the machine's app list, and
    # a test must not write it into the user's profile or read theirs.
    monkeypatch.setenv("TORA_APPS_CACHE", str(tmp_path / "installed_apps.json"))


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
