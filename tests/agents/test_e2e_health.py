"""The health panel (EN-17, the Massive Direction Plan).

One page shows the models, the microphone and Windows' permission for it,
connectors, disk headroom, each database's version and the last update -
all read locally. The registry and the real data folder are never touched:
the permission is read through a stand-in, the databases from tmp_path.
"""
from __future__ import annotations

import sqlite3

import health


def test_windows_microphone_switch_is_read(monkeypatch):
    values = {"": "Allow", "NonPackaged": "Deny"}
    monkeypatch.setattr(health, "_consent", lambda sub="": values[sub])
    assert health.mic_permission() == ("Microphone permission",
                                       "Off for desktop apps in Windows privacy settings", "bad")
    values["NonPackaged"] = "Allow"
    assert health.mic_permission()[2] == "ok"
    values[""] = "Deny"
    assert "every app" in health.mic_permission()[1]


def test_disk_and_databases(tmp_path):
    name, detail, level = health.disk(tmp_path)
    assert name == "Disk space" and "GB free" in detail and level in ("ok", "warn", "bad")
    conn = sqlite3.connect(tmp_path / "agents.db")
    conn.execute("create table tasks (id text)")
    conn.execute("pragma user_version = 4")
    conn.commit()
    conn.close()
    (tmp_path / "broken.db").write_bytes(b"not a database at all" * 50)
    rows = {n: (d, lv) for n, d, lv in health.databases(tmp_path)}
    assert rows["Database agents.db"][0].startswith("v4 · 1 tables") and rows["Database agents.db"][1] == "ok"
    assert rows["Database broken.db"][1] == "bad"


def test_the_last_update_is_said(monkeypatch):
    import time

    from updates import service
    fake = type("U", (), {"state": lambda self: {"state": "current", "current": "1.7.15", "latest": "1.7.16",
                                                  "checked_at": time.time() - 7200}})()
    monkeypatch.setattr(service, "updater", lambda: fake)
    name, detail, level = health.last_update()
    assert detail == "1.7.15 · checked 2 h ago · 1.7.16 is out" and level == "warn"
