"""SQLiteMemoryStore + AgentMemory: query filters, text search, summary budget."""
import pytest

from agents.core import MemoryEntry, MemoryStore, Task, TaskStatus
from agents.memory import AgentMemory, SQLiteMemoryStore, has_fts5
from agents.memory import store as store_mod

WS = "C:/proj/MyApp"


def _entry(kind="context", content="x", tags=(), scope=None, **data) -> MemoryEntry:
    return MemoryEntry(scope=scope or MemoryStore.project_scope(WS), kind=kind,
                       content=content, tags=list(tags), data=data)


def test_add_and_query_by_scope_kind_tags():
    s = SQLiteMemoryStore()
    s.add(_entry("context", "framework: React", tags=["context", "framework"]))
    s.add(_entry("error", "npm failed", tags=["error", "npm"]))
    s.add(_entry("error", "pip failed", tags=["error", "pip"]))
    s.add(_entry("error", "elsewhere", scope="project:other"))
    scope = MemoryStore.project_scope(WS)
    assert len(s.query(scope)) == 3
    assert [e.content for e in s.query(scope, kind="context")] == ["framework: React"]
    assert {e.content for e in s.query(scope, tags=["npm", "pip"])} == {"npm failed", "pip failed"}
    assert [e.content for e in s.query(scope, kind="error", tags=["pip"])] == ["pip failed"]
    assert s.query(scope, tags=["nothing"]) == []
    assert s.query("project:other")[0].content == "elsewhere"


def test_query_returns_newest_first_with_limit():
    s = SQLiteMemoryStore()
    for i in range(5):
        s.add(MemoryEntry(scope="user", kind="preference", content=f"p{i}", created_at=1000 + i))
    got = s.query("user", limit=3)
    assert [e.content for e in got] == ["p4", "p3", "p2"]


def test_roundtrip_preserves_tags_and_data():
    s = SQLiteMemoryStore()
    e = _entry("decision", "use vite", tags=["decision"], reason="fast", count=2)
    s.add(e)
    got = s.query(MemoryStore.project_scope(WS))[0]
    assert got.id == e.id and got.tags == ["decision"]
    assert got.data == {"reason": "fast", "count": 2}
    assert got.created_at == e.created_at


@pytest.mark.parametrize("use_fts", [True, False])
def test_text_search_fts_and_like(monkeypatch, use_fts):
    if use_fts and not has_fts5():
        pytest.skip("sqlite without FTS5")
    monkeypatch.setattr(store_mod, "_has_fts5", use_fts)
    s = SQLiteMemoryStore()
    assert s.fts is use_fts
    scope = MemoryStore.project_scope(WS)
    s.add(_entry("error", "npm install failed with EACCES on node_modules", tags=["npm"]))
    s.add(_entry("error", "pytest could not import package", tags=["python"]))
    s.add(_entry("context", "framework: React with Vite", tags=["framework"]))
    assert [e.content for e in s.query(scope, text="EACCES")] == ["npm install failed with EACCES on node_modules"]
    assert [e.content for e in s.query(scope, text="npm install")][0].startswith("npm install")
    assert len(s.query(scope, text="vite react")) == 1          # order-independent, both words
    assert s.query(scope, text="nothing-here") == []
    assert s.query(scope, text='weird "quotes" : and * chars') == []   # never raises
    assert [e.content for e in s.query(scope, kind="context", text="react")] == ["framework: React with Vite"]


def test_clear_scope_only_removes_that_scope():
    s = SQLiteMemoryStore()
    s.add(_entry("context", "a"))
    s.add(MemoryEntry(scope="user", kind="preference", content="keep"))
    s.clear(MemoryStore.project_scope(WS))
    assert s.count(MemoryStore.project_scope(WS)) == 0
    assert s.query("user")[0].content == "keep"
    assert s.query(MemoryStore.project_scope(WS), text="a") == []


def test_project_summary_sections_and_trimming():
    s = SQLiteMemoryStore()
    m = AgentMemory(s)
    m.remember_context(WS, "framework", "React + Vite")
    m.remember_context(WS, "framework", "React 19 + Vite 6")       # newer value wins
    m.remember_context(WS, "package_manager", "npm")
    done = Task(title="Scaffold app", goal="g", workspace=WS, status=TaskStatus.COMPLETED)
    failed = Task(title="Deploy", goal="g", workspace=WS, status=TaskStatus.FAILED)
    m.remember_task(done)
    m.remember_task(failed)
    m.remember_decision(WS, "Use plain CSS, no Tailwind")
    m.remember_error(WS, "npm install: EACCES", solution="fix permissions on node_modules")
    m.remember_error(WS, "vite: port in use")

    text = s.project_summary(WS)
    assert "Project context:" in text
    assert "- framework: React 19 + Vite 6" in text and text.count("framework:") == 1
    assert "- package_manager: npm" in text
    assert "Done:" in text and "Scaffold app" in text and "Deploy" not in text
    assert "Current / decisions:" in text and "Use plain CSS" in text
    assert "Recent errors:" in text
    assert "- npm install: EACCES -> fixed by: fix permissions on node_modules" in text
    assert "- vite: port in use" in text

    short = s.project_summary(WS, max_chars=60)
    assert len(short) <= 60 and short.startswith("Project context:")
    assert "\n" in short                                   # whole lines, not a hard cut
    tiny = s.project_summary(WS, max_chars=10)
    assert len(tiny) <= 10 and tiny.endswith("…")
    assert s.project_summary("C:/proj/empty") == ""


def test_agent_memory_context_for_includes_preferences():
    s = SQLiteMemoryStore()
    m = AgentMemory(s)
    m.remember_preference("Prefers TypeScript over JavaScript")
    m.remember_context(WS, "framework", "React")
    text = m.context_for(WS)
    assert "framework: React" in text and "Prefers TypeScript" in text
    assert text.index("Project context") < text.index("User preferences")
    assert len(m.context_for(WS, max_chars=80)) <= 80
    assert m.preferences()[0].scope == "user"
    assert m.errors_for(WS) == []
    m.remember_error(WS, "boom")
    assert [e.content for e in m.errors_for(WS, text="boom")] == ["boom"]
    m.forget_project(WS)
    assert s.query(MemoryStore.project_scope(WS)) == []
    assert m.preferences()                                 # user scope untouched


def test_memory_store_on_disk_shares_path_with_task_store(tmp_path):
    from agents.persistence import SQLiteTaskStore
    path = str(tmp_path / "agents.db")
    ts = SQLiteTaskStore(path)
    ms = SQLiteMemoryStore(path)
    ms.add(MemoryEntry(scope="user", kind="preference", content="dark mode"))
    ts.close()
    ms.close()
    again = SQLiteMemoryStore(path)
    assert again.query("user")[0].content == "dark mode"
    again.close()
