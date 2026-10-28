"""TORA_DATA_DIR: a run against the local Supabase (tools/dev.py) keeps its
sign-in and history in a folder of its own, so the installed HeyTora stays
signed in to the live service."""
import config


def test_data_dir_is_localappdata_heytora_by_default(tmp_path):
    assert config.data_dir() == tmp_path / "LocalAppData" / "HeyTora"


def test_tora_data_dir_replaces_it_and_moves_nothing(tmp_path, monkeypatch):
    old = tmp_path / "LocalAppData" / "Tora"
    old.mkdir(parents=True)
    (old / "journal.db").write_text("kept")
    own = tmp_path / "HeyTora-dev"
    monkeypatch.setenv("TORA_DATA_DIR", str(own))

    assert config.data_dir() == own
    assert own.is_dir()
    # the old folder is the live app's business, not the dev run's
    assert (old / "journal.db").read_text() == "kept"
    assert config.MIGRATION_NOTES == []
