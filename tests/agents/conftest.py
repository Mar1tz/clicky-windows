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
def _own_key_by_default(monkeypatch):
    """With no key in the environment the app would choose a HeyTora account
    (account/), and every test that fakes Google's HTTP would be sent to a
    server that is not there. Tests run as a developer does - own key - and
    the account tests switch to "account" themselves. The profile from the
    first launch starts empty too."""
    monkeypatch.setenv("TORA_ACCESS", "own_key")
    monkeypatch.delenv("TORA_USER_NAME", raising=False)
    monkeypatch.delenv("TORA_USER_USES", raising=False)
    for name in ("TORA_SUPABASE_URL", "TORA_SUPABASE_ANON_KEY", "TORA_WEBSITE_URL"):
        monkeypatch.delenv(name, raising=False)
    try:
        # The build carries the real project's address; a test never reaches
        # it. Account tests point TORA_SUPABASE_URL at their fake server.
        from account import service, settings
        for name in ("_SUPABASE_URL", "_SUPABASE_ANON_KEY", "_WEBSITE_URL"):
            monkeypatch.setattr(settings, name, "")
        service.reset(None)
    except Exception:  # noqa: BLE001 - a suite without the app still runs
        pass


@pytest.fixture(autouse=True)
def _no_real_monitors(monkeypatch):
    """screen.monitors reads the machine's real monitors - three of them, at
    100% and 125%, on the PC this was written on - so a test's pixels would
    depend on where it runs. Every test gets none (pixels pass straight
    through); the monitor tests give their own layout."""
    try:
        from screen import monitors
        monkeypatch.setattr(monitors, "monitors", lambda: [])
    except Exception:  # noqa: BLE001 - a suite without the app still runs
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
    # And the .env: it holds the user's real API keys, and saved switches
    # (APP-03) are written into it. No test may touch the real one - every
    # write goes to this test's own copy, and the switches start from their
    # defaults, not from whatever the developer last clicked.
    try:
        import config as config_module
        monkeypatch.setattr(config_module.Config, "env_path",
                            staticmethod(lambda: tmp_path / ".env"))
        for env, _default in config_module.Config.SWITCHES.values():
            monkeypatch.delenv(env, raising=False)
    except Exception:  # noqa: BLE001 - a suite without the app still runs
        pass


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


@pytest.fixture(autouse=True)
def _windows_voice_is_scripted(monkeypatch):
    """The Windows voice (LG-04) is real SAPI: a second to start, and not on
    every machine the suite runs on. Every test gets 0.1 s of silence from it
    instead; nothing here ever drives the real one."""
    try:
        from audio.tts import windows_voice
        monkeypatch.setattr(windows_voice.WindowsVoice, "_render",
                            lambda self, text: b"\x00\x00" * 2400)
        monkeypatch.setattr(windows_voice, "_voice", None)
    except Exception:  # noqa: BLE001 - a suite without the app still runs
        pass


@pytest.fixture(autouse=True)
def _no_real_keyboard_hooks(monkeypatch):
    """A shortcut step (LG-08) registers its keys with the global keyboard
    hook. No test may install a real one on the machine running the suite."""
    try:
        from ai import verification
        monkeypatch.setattr(verification, "_keyboard_hook",
                            lambda combo, on_press: (lambda: None))
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _data_folder_in_the_sandbox(tmp_path, monkeypatch):
    r"""config.data_dir() is %LOCALAPPDATA%\HeyTora, and settling it MOVES an
    old Tora folder into it. Run against the real profile, the first test to
    ask for it would move the user's journal and agents' history. Every test
    gets its own LocalAppData instead, decided afresh."""
    try:
        import config
        monkeypatch.setattr(config, "_appdata_base", lambda: tmp_path / "LocalAppData")
        monkeypatch.setattr(config, "_DATA_DIR", None)
        monkeypatch.setattr(config, "MIGRATION_NOTES", [])
    except Exception:  # noqa: BLE001 - a suite without the app still runs
        pass


@pytest.fixture(autouse=True)
def _no_model_is_resting(monkeypatch):
    """FAST-01: a model that failed is skipped for five minutes - module-wide.
    Each test starts with every model available, whatever the last one did."""
    try:
        from ai import gemini_vision
        monkeypatch.setattr(gemini_vision, "_cool_until", {})
    except Exception:  # noqa: BLE001
        pass
