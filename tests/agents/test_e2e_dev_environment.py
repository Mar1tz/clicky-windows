"""The development environment keeps real users safe (3 Nov 2026).

The live address is the app's default, so `python main.py` in a checkout
acted on real accounts and credits. Now a source run refuses live unless told
otherwise, tools/dev.py refuses anything not on this PC and its address beats
what is in .env, a pulled migration is applied by itself, and the website's
local address can never reach a production build. No Docker, no network.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def dev(monkeypatch):
    spec = importlib.util.spec_from_file_location("dev_tool", ROOT / "tools" / "dev.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- running from source ----------------------------------------------------------------------------------------

def test_a_source_run_refuses_live_unless_told(monkeypatch):
    from account import settings
    monkeypatch.delenv("TORA_SUPABASE_URL", raising=False)
    monkeypatch.delenv("TORA_ALLOW_LIVE", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(settings, "_SUPABASE_URL", "https://live.supabase.co")   # the tests blank the real one
    assert settings.live_from_source_blocked(), "the default address is the live one"
    assert "python tools/dev.py" in settings.LIVE_BLOCKED_WORDS
    monkeypatch.setenv("TORA_ALLOW_LIVE", "1")
    assert not settings.live_from_source_blocked(), "said on purpose"


def test_the_local_service_and_installed_builds_are_never_stopped(monkeypatch):
    from account import settings
    monkeypatch.delenv("TORA_ALLOW_LIVE", raising=False)
    monkeypatch.setenv("TORA_SUPABASE_URL", "http://127.0.0.1:57321")
    assert settings.is_local() and not settings.live_from_source_blocked()
    monkeypatch.delenv("TORA_SUPABASE_URL")
    monkeypatch.setattr(settings, "_SUPABASE_URL", "https://live.supabase.co")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert not settings.is_local() and not settings.live_from_source_blocked(), "what users run"


def test_main_checks_before_anything_starts():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    body = src[src.index("def main():"):]
    assert body.index("live_from_source_blocked()") < body.index("_setup_logging()") < body.index("QApplication(")


def test_a_line_in_dotenv_cannot_send_a_dev_run_to_live(tmp_path):
    """config.py loads .env with override=True: simulated here by a load_dotenv
    that sets the live address, as such a line would."""
    code = (
        "import os, dotenv\n"
        "dotenv.load_dotenv = lambda *a, **k: os.environ.__setitem__('TORA_SUPABASE_URL', 'https://live.supabase.co')\n"
        "import config\n"
        "print(os.environ['TORA_SUPABASE_URL'])\n"
    )
    base = {k: v for k, v in __import__("os").environ.items() if k != "TORA_DEV"}
    base["TORA_DATA_DIR"] = str(tmp_path)
    base["TORA_SUPABASE_URL"] = "http://127.0.0.1:57321"
    dev_run = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=base | {"TORA_DEV": "1"},
                             capture_output=True, text=True, check=True)
    assert dev_run.stdout.strip().endswith("http://127.0.0.1:57321")
    plain = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=base, capture_output=True, text=True,
                           check=True)
    assert plain.stdout.strip().endswith("https://live.supabase.co"), "only a dev run is protected"


# -- tools/dev.py ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url, local", [
    ("http://127.0.0.1:57321", True), ("http://localhost:57321", True),
    ("https://uizwfpxzzkokyrkvjzkr.supabase.co", False), ("https://127.0.0.1.evil.example", False), ("", False),
])
def test_only_this_pc_counts_as_local(dev, url, local):
    assert dev.is_local_url(url) is local


def test_dev_refuses_to_run_against_anything_else(dev, monkeypatch):
    monkeypatch.setattr(dev, "cmd_setup", lambda: 0)
    monkeypatch.setattr(dev, "dotenv_values", lambda *_a, **_k: {"TORA_SUPABASE_URL": "https://x.supabase.co"})
    monkeypatch.setattr(dev.subprocess, "Popen", lambda *a, **k: pytest.fail("started something"))
    with pytest.raises(SystemExit, match="not on this PC"):
        dev.cmd_run()


def test_a_live_address_in_the_dev_file_stops_setup(dev, monkeypatch, tmp_path):
    env = tmp_path / ".env.development"
    env.write_text("TORA_SUPABASE_URL=https://x.supabase.co\n", encoding="utf-8")
    monkeypatch.setattr(dev, "ENV_FILE", env)
    with pytest.raises(SystemExit, match="points at live"):
        dev.write_env({"API_URL": "http://127.0.0.1:57321", "ANON_KEY": "k"})


def test_what_is_missing_and_what_is_extra(dev):
    rows = [{"local": "0013", "remote": "0013"}, {"local": "0014", "remote": ""}, {"local": "", "remote": "0099"}]
    assert dev.migration_gaps(rows) == (["0014"], ["0099"])


def test_a_pulled_migration_is_applied_to_the_local_database_only(dev, monkeypatch):
    calls = []

    def fake(args, check=True, capture=False):
        calls.append(args)
        rows = [{"local": "0015", "remote": ""}] if "list" in args else []
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"migrations": rows}), stderr="")
    monkeypatch.setattr(dev, "supabase", fake)
    dev.catch_up_migrations()
    assert calls == ["migration list --local --output-format json", "migration up --local"]
    assert not any("linked" in c or "project-ref" in c or "db push" in c for c in calls)


def test_a_function_asking_who_is_calling_is_an_answer(dev, monkeypatch):
    import urllib.error

    def said(code):
        def open_it(*_a, **_k):
            raise urllib.error.HTTPError("u", code, "m", None, None)
        return open_it
    monkeypatch.setattr(dev.urllib.request, "urlopen", said(401))
    assert dev.functions_answering("http://127.0.0.1:1")
    monkeypatch.setattr(dev.urllib.request, "urlopen", said(503))
    assert not dev.functions_answering("http://127.0.0.1:1")


def test_a_reset_forgets_the_sign_in_it_just_wiped(dev, monkeypatch, tmp_path):
    kept = tmp_path / "account.bin"
    kept.write_text("old session", encoding="utf-8")
    monkeypatch.setattr(dev, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dev, "ensure_stack", lambda: {})
    monkeypatch.setattr(dev, "supabase", lambda *a, **k: None)
    assert dev.cmd_reset() == 0 and not kept.exists()


def test_the_flags_reach_the_run(dev, monkeypatch):
    got = {}
    monkeypatch.setattr(dev, "cmd_run", lambda email, tour, out: got.update(email=email, tour=tour, out=out) or 0)
    monkeypatch.setattr(sys, "argv", ["dev.py", "--as", "pro@heytora.test", "--tour"])
    assert dev.main() == 0 and got == {"email": "pro@heytora.test", "tour": True, "out": False}


def test_the_seed_has_an_account_for_each_case():
    seed = (ROOT / "supabase" / "seed.sql").read_text(encoding="utf-8")
    for who in ("free", "pro", "max", "out"):
        assert f"{who}@heytora.test" in seed
    assert "public.usage_costs" in seed, "out@ has really spent its month"


# -- the website -------------------------------------------------------------------------------------------------

def test_the_website_gets_a_file_a_production_build_never_reads(dev, monkeypatch, tmp_path):
    """Next's .env.local is read by builds too: the local address would ship
    to real users. .env.development.local is `next dev` only."""
    app = tmp_path / "heytora-app"
    web = tmp_path / "heytora-web"
    app.mkdir()
    web.mkdir()
    (web / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dev, "ROOT", app)
    dev.write_website_env({"API_URL": "http://127.0.0.1:57321", "ANON_KEY": "local-anon"})
    written = (web / ".env.development.local").read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:57321" in written and "local-anon" in written
    assert not (web / ".env.local").exists() and not (web / ".env.production").exists()


def test_no_website_next_door_is_fine(dev, monkeypatch, tmp_path):
    monkeypatch.setattr(dev, "ROOT", tmp_path / "heytora-app")
    dev.write_website_env({"API_URL": "x", "ANON_KEY": "y"})
