"""AG-09: the SQLite write-ahead logs are folded in, at open and at close.

agent_memory.db was 4 KB beside a 1 MB log that had not been folded in since
11 Oct: SQLite only checkpoints as pages pile up, and these stores are written
a little at a time.
"""
from __future__ import annotations

import os

from agents.core import MemoryEntry, Task
from agents.memory.store import SQLiteMemoryStore
from agents.persistence.sqlite_store import SQLiteTaskStore


def _wal(path) -> int:
    return os.path.getsize(f"{path}-wal") if os.path.exists(f"{path}-wal") else 0


def test_the_task_store_leaves_no_log_behind(tmp_path):
    db = tmp_path / "agents.db"
    store = SQLiteTaskStore(str(db))
    for i in range(30):
        store.save_task(Task(title=f"job {i}", goal="g" * 2000, workspace=str(tmp_path)))
    assert _wal(db) > 0, "written through the log"
    store.close()
    assert _wal(db) == 0


def test_the_memory_store_folds_an_old_log_in_when_it_opens(tmp_path):
    db = tmp_path / "agent_memory.db"
    first = SQLiteMemoryStore(str(db))
    for i in range(30):
        first.add(MemoryEntry(scope="user", kind="context", content=f"note {i} " + "v" * 2000))
    # Left open, as a crash or a kill leaves it: the log keeps the writes.
    assert _wal(db) > 0
    second = SQLiteMemoryStore(str(db))
    try:
        assert _wal(db) == 0, "folded in on open"
        assert second.count("user") == 30, "nothing lost"
    finally:
        second.close()
        first.close()


# -- AG-01: a saved job keeps what a person needs, not its bulk ----------------------------------------------------

def test_a_saved_job_drops_its_screenshots_and_keeps_a_head_of_long_text(tmp_path):
    from agents.core import Step, StepStatus, ToolCall, ToolResult
    picture = "iVBORw0KGgo" + "A" * 900_000
    page = "Dear Amy, " + "lorem ipsum " * 40_000
    task = Task(title="Help me respond to this email", goal="Reply to Amy", workspace=str(tmp_path), steps=[
        Step("Read the email", status=StepStatus.DONE,
             tool_calls=[ToolCall("read_page", {"url": "https://mail.example"})],
             results=[ToolResult(call_id="c", tool_name="screenshot", ok=True, output=page,
                                 data={"image_b64": picture, "path": "shot.png"})])])
    task.result = "Sent the reply: " + "x" * 5000
    store = SQLiteTaskStore(str(tmp_path / "agents.db"))
    store.save_task(task)
    with store._lock:
        raw = store._conn.execute("select json from tasks").fetchone()[0]
    assert len(raw) < 20_000, f"{len(raw)} bytes"
    back = store.get_task(task.id)
    res = back.steps[0].results[0]
    assert res.output.startswith("Dear Amy, lorem") and "more characters not kept" in res.output
    assert res.data["image_b64"].startswith("[picture, ") and res.data["path"] == "shot.png"
    assert back.result == task.result, "the result is kept whole"
    assert task.steps[0].results[0].data["image_b64"] == picture, "the running job keeps everything"
    store.close()


def test_a_saved_event_drops_its_bulk_too(tmp_path):
    from agents.core import Event, EventKind
    store = SQLiteTaskStore(str(tmp_path / "agents.db"))
    store.append_event(Event(kind=EventKind.TOOL_RESULT, task_id="t", data={"result": {
        "output": "x" * 100_000, "data": {"image_b64": "A" * 500_000}}}))
    with store._lock:
        raw = store._conn.execute("select json from events").fetchone()[0]
    assert len(raw) < 5_000 and "not kept" in raw
    store.close()
