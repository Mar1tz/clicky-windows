"""M16: connected accounts and routing.

The invariant worth defending is that a key never reaches the accounts
file — `test_the_key_never_lands_in_the_accounts_file` reads the JSON off
disk and asserts the secret is absent.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from agents.core import ConnectedAccount, TaskKind  # noqa: E402
from agents.providers import (  # noqa: E402
    AccountStore, GATEWAY_MODELS, MockProvider, OpenAICompatibleProvider,
    build_router, provider_from_account,
)
from agents.ui.accounts import ConnectedAccountsDialog  # noqa: E402

SECRET = "sk-super-secret-value-1234"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


class FakeConfig:
    """Captures what would be written to the environment and .env."""

    def __init__(self):
        self.written: dict[str, str] = {}

    def set_agent_env(self, name, value):
        self.written[name] = value
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)


@pytest.fixture
def store(tmp_path):
    return AccountStore(str(tmp_path / "accounts.json"))


# -- the model ---------------------------------------------------------------

def test_account_derives_its_env_variable_names():
    a = ConnectedAccount(provider="omniroute")
    assert a.env_key_name == "TORA_OMNIROUTE_API_KEY"
    assert a.env_url_name == "TORA_OMNIROUTE_BASE_URL"


def test_account_roundtrips_the_new_fields():
    a = ConnectedAccount(provider="gw", base_url="https://x/v1",
                         models=["a", "b"], kinds=["coding"])
    b = ConnectedAccount.from_dict(a.to_dict())
    assert b.base_url == "https://x/v1" and b.models == ["a", "b"] and b.kinds == ["coding"]


def test_provider_from_account_uses_the_chain():
    a = ConnectedAccount(provider="gw", base_url="https://x/v1", models=["m1", "m2"])
    p = provider_from_account(a, key="k")
    assert p.base_url == "https://x/v1" and p.model == "m1" and p.fallback_models == ["m2"]
    assert p.api_key == "k" and p.name == "gw"


def test_provider_from_account_skips_unusable_rows():
    assert provider_from_account(ConnectedAccount(provider="gw", base_url="")) is None
    assert provider_from_account(
        ConnectedAccount(provider="gw", base_url="https://x/v1", enabled=False)) is None


def test_account_without_models_falls_back_to_defaults():
    a = ConnectedAccount(provider="gw", base_url="https://x/v1")
    assert provider_from_account(a).model == GATEWAY_MODELS["general"][0]


# -- routing -----------------------------------------------------------------

def test_saved_accounts_outrank_the_environment(store, monkeypatch):
    monkeypatch.setenv("TORA_OMNIROUTE_BASE_URL", "https://from-env/v1")
    store.add(ConnectedAccount(provider="mine", base_url="https://from-account/v1",
                               models=["x/model"]))
    router = build_router(store)
    picked = router.pick(TaskKind.GENERAL)
    assert isinstance(picked, OpenAICompatibleProvider)
    assert picked.base_url == "https://from-account/v1"


def test_kind_scoped_account_routes_only_that_kind(store):
    store.add(ConnectedAccount(provider="gw", base_url="https://x/v1", models=["general/m"]))
    store.add(ConnectedAccount(provider="gw", base_url="https://x/v1",
                               models=["coding/m"], kinds=["coding"]))
    router = build_router(store)
    assert router.pick(TaskKind.CODING).model == "coding/m"
    assert router.pick(TaskKind.GENERAL).model == "general/m"


def test_disabled_account_is_not_registered(store):
    store.add(ConnectedAccount(provider="gw", base_url="https://x/v1", enabled=False))
    router = build_router(store, gateway=True)
    assert all(isinstance(p, MockProvider) for p in router.providers())


# -- the dialog --------------------------------------------------------------

def test_dialog_seeds_from_the_environment(qapp, store, monkeypatch):
    monkeypatch.setenv("TORA_OMNIROUTE_BASE_URL", "https://seeded/v1")
    dlg = ConnectedAccountsDialog(None, store=store, config=FakeConfig())
    assert len(dlg._cards) == 1
    assert dlg._cards[0].url.text() == "https://seeded/v1"


def test_dialog_shows_saved_endpoints(qapp, store):
    store.add(ConnectedAccount(provider="mine", base_url="https://saved/v1"))
    dlg = ConnectedAccountsDialog(None, store=store, config=FakeConfig())
    assert [c.url.text() for c in dlg._cards] == ["https://saved/v1"]


def test_dialog_shows_the_routing_chains(qapp, store):
    dlg = ConnectedAccountsDialog(None, store=store, config=FakeConfig())
    assert dlg.chains["coding"].text() == ", ".join(GATEWAY_MODELS["coding"])
    assert set(dlg.chains) == {"coding", "general", "fast", "vision"}


def test_save_writes_endpoint_chains_and_key(qapp, store, tmp_path):
    cfg = FakeConfig()
    dlg = ConnectedAccountsDialog(None, store=store, config=cfg)
    card = dlg.add_card(ConnectedAccount(provider="custom"))
    card.name.setText("mygw")
    card.url.setText("https://mygw.test/v1")
    card.key.setText(SECRET)
    dlg.chains["coding"].setText("a/one, b/two")

    saved = []
    dlg.saved.connect(lambda: saved.append(True))
    dlg.save()
    assert saved

    assert cfg.written["TORA_MYGW_API_KEY"] == SECRET
    assert cfg.written["TORA_MYGW_BASE_URL"] == "https://mygw.test/v1"
    reloaded = AccountStore(store.path)
    assert any(a.base_url == "https://mygw.test/v1" for a in reloaded.list())
    coding = next(a for a in reloaded.list() if a.kinds == ["coding"])
    assert coding.models == ["a/one", "b/two"]


def test_the_key_never_lands_in_the_accounts_file(qapp, store):
    """The whole reason keys go to .env instead: this file gets synced,
    backed up and pasted into bug reports."""
    dlg = ConnectedAccountsDialog(None, store=store, config=FakeConfig())
    card = dlg.add_card(ConnectedAccount(provider="custom"))
    card.name.setText("gw")
    card.url.setText("https://gw.test/v1")
    card.key.setText(SECRET)
    dlg.save()

    raw = open(store.path, encoding="utf-8").read()
    assert SECRET not in raw, "an API key was written to the accounts file"
    assert "https://gw.test/v1" in raw
    assert SECRET not in json.dumps(json.loads(raw))


def test_removing_a_card_removes_the_account(qapp, store):
    store.add(ConnectedAccount(provider="gone", base_url="https://gone/v1"))
    dlg = ConnectedAccountsDialog(None, store=store, config=FakeConfig())
    assert len(dlg._cards) == 1
    dlg._remove_card(dlg._cards[0])
    assert dlg._cards == []
    assert AccountStore(store.path).list() == []


def test_test_button_reports_failure_without_raising(qapp, store):
    dlg = ConnectedAccountsDialog(None, store=store, config=FakeConfig())
    card = dlg.add_card(ConnectedAccount(provider="nope",
                                         base_url="http://127.0.0.1:9/v1"))
    dlg.test(card)                       # nothing is listening on port 9
    assert "failed" in card.state.text().lower()


def test_test_button_needs_a_url(qapp, store):
    dlg = ConnectedAccountsDialog(None, store=store, config=FakeConfig())
    card = dlg.add_card(ConnectedAccount(provider="blank", base_url=""))
    dlg.test(card)
    assert card.state.text() == "no URL"


def test_apply_to_rebuilds_the_router(qapp, store, tmp_path):
    from agents.bootstrap import build_agent_manager
    mgr = build_agent_manager(workspace_root=str(tmp_path), in_memory=True, offline=True)
    try:
        assert all(isinstance(p, MockProvider) for p in mgr.router.providers())
        store.add(ConnectedAccount(provider="gw", base_url="https://new/v1",
                                   models=["m/1"]))
        ConnectedAccountsDialog.apply_to(mgr, store)
        assert any(isinstance(p, OpenAICompatibleProvider) for p in mgr.router.providers())
    finally:
        mgr.stop()


def test_config_set_agent_env_rejects_foreign_names(tmp_path, monkeypatch):
    """The prefix check is the guard that keeps this from writing anything."""
    import config as config_module
    monkeypatch.setattr(config_module.Config, "env_path",
                        staticmethod(lambda: tmp_path / ".env"))
    cfg = config_module.Config()
    cfg.set_agent_env("PATH", "malicious")
    assert os.environ.get("PATH") != "malicious"
    cfg.set_agent_env("TORA_TESTGW_API_KEY", "abc")
    assert os.environ["TORA_TESTGW_API_KEY"] == "abc"
    assert "TORA_TESTGW_API_KEY=abc" in (tmp_path / ".env").read_text(encoding="utf-8")
    cfg.set_agent_env("TORA_TESTGW_API_KEY", "")
    assert "TORA_TESTGW_API_KEY" not in os.environ
