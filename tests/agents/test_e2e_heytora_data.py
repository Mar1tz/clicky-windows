r"""HeyTora 3/3: the data folder moves from %LOCALAPPDATA%\Tora to \HeyTora -
and nothing the user built up is lost on the way.

What lives there: the knowledge journal, the agents' task history and
memory (SQLite, with a -wal of changes not yet written into the database),
the desktop permissions they granted, the setup flag, the log. The move is
one rename when it can be; when the old app still has the files open, this
run uses the old folder rather than split them across two.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import config


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    base = tmp_path / "LocalAppData"
    base.mkdir()
    monkeypatch.setattr(config, "_appdata_base", lambda: base)
    monkeypatch.setattr(config, "_DATA_DIR", None)
    monkeypatch.setattr(config, "MIGRATION_NOTES", [])
    return base


def _old_tora(base: Path) -> Path:
    """A Tora folder shaped like the real one on 26 Sep."""
    old = base / "Tora"
    (old / "extensions").mkdir(parents=True)
    for name, body in (("journal.db", "J"), ("agents.db", "A"), ("agent_memory.db", "M"),
                       ("agent_memory.db-wal", "unsaved changes"), ("agent_memory.db-shm", "S"),
                       ("desktop_grants.json", "[]"), ("setup_complete.flag", "1"),
                       ("tora.log", "log")):
        (old / name).write_text(body, encoding="utf-8")
    return old


def test_a_fresh_machine_gets_a_heytora_folder(appdata):
    assert config.data_dir() == appdata / "HeyTora" and (appdata / "HeyTora").is_dir()
    assert config.MIGRATION_NOTES == []


def test_the_tora_folder_moves_whole(appdata):
    _old_tora(appdata)
    folder = config.data_dir()
    assert folder == appdata / "HeyTora"
    assert not (appdata / "Tora").exists()
    assert (folder / "agent_memory.db-wal").read_text(encoding="utf-8") == "unsaved changes"
    assert (folder / "journal.db").exists() and (folder / "extensions").is_dir()
    assert any("moved" in n for n in config.MIGRATION_NOTES)


def test_while_the_old_app_has_it_open_the_old_folder_is_used(appdata, monkeypatch):
    """Nothing is split: this run stays on Tora, and the move waits."""
    old = _old_tora(appdata)
    real_rename = Path.rename

    def locked(self, target):
        if self == old:
            raise PermissionError(32, "The process cannot access the file")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", locked)
    assert config.data_dir() == old
    assert not (appdata / "HeyTora").exists()
    assert (old / "journal.db").exists()
    assert any("in use" in n for n in config.MIGRATION_NOTES)


def test_the_folder_is_decided_once_per_run(appdata):
    first = config.data_dir()
    _old_tora(appdata)                      # appears later: not this run's business
    assert config.data_dir() == first


def test_when_both_exist_only_what_is_missing_moves(appdata):
    _old_tora(appdata)
    new = appdata / "HeyTora"
    new.mkdir()
    (new / "journal.db").write_text("NEWER", encoding="utf-8")
    folder = config.data_dir()
    assert (folder / "journal.db").read_text(encoding="utf-8") == "NEWER", "never overwritten"
    assert (folder / "agents.db").read_text(encoding="utf-8") == "A"
    assert (folder / "agent_memory.db-wal").exists()


def test_a_database_never_moves_without_its_unsaved_changes(appdata, monkeypatch):
    """If the -wal will not move, the database it belongs to goes back."""
    old = _old_tora(appdata)
    (appdata / "HeyTora").mkdir()
    real_move = shutil.move

    def wal_locked(src, dst):
        if str(src).endswith("-wal"):
            raise PermissionError(32, "in use")
        return real_move(src, dst)

    monkeypatch.setattr(config.shutil, "move", wal_locked)
    folder = config.data_dir()
    assert (old / "agent_memory.db").exists() and (old / "agent_memory.db-wal").exists()
    assert not (folder / "agent_memory.db").exists(), "not half a database"
    assert (folder / "journal.db").exists(), "everything else still moved"


def test_the_clicky_folder_before_that_still_comes_across(appdata):
    clicky = appdata / "Clicky"
    clicky.mkdir()
    (clicky / "journal.db").write_text("J", encoding="utf-8")
    (clicky / "clicky.log").write_text("log", encoding="utf-8")
    folder = config.data_dir()
    assert (folder / "journal.db").exists() and (folder / "tora.log").exists()
    assert not clicky.exists()


def test_the_agents_keep_their_history_beside_the_journal(appdata):
    """agents.db used to be placed by the agents themselves; a failed move
    would have put it in one folder and the journal in another."""
    from agents.bootstrap import data_dir as agents_data_dir
    assert Path(agents_data_dir()) == config.data_dir()
