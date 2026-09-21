"""SQLiteTaskStore: roundtrips, bus capture, ordering, threaded writes."""
import threading
import time

from agents.core import (
    AgentSpec, AgentState, AgentStatus, Event, EventBus, EventKind, Plan, Step,
    StepStatus, Task, TaskKind, TaskStatus, ToolCall, ToolResult,
)
from agents.persistence import SQLiteTaskStore


def _task(title="Build", **kw) -> Task:
    kw.setdefault("goal", "build a react app")
    kw.setdefault("workspace", "C:/tmp/ws")
    return Task(title=title, **kw)


def test_task_roundtrip_with_nested_steps():
    store = SQLiteTaskStore(":memory:")
    call = ToolCall("create_file", {"path": "a.txt", "content": "hi"})
    t = _task(kind=TaskKind.CODING, metadata={"subdir": "MyApp", "install": False},
              steps=[Step("one", tool_calls=[call],
                          results=[ToolResult.success(call, "ok", path="a.txt")],
                          status=StepStatus.DONE)])
    t.status = TaskStatus.RUNNING
    store.save_task(t)
    got = store.get_task(t.id)
    assert got is not None and got.id == t.id
    assert got.status is TaskStatus.RUNNING and got.kind is TaskKind.CODING
    assert got.metadata == {"subdir": "MyApp", "install": False}
    assert isinstance(got.steps[0], Step) and got.steps[0].status is StepStatus.DONE
    assert isinstance(got.steps[0].tool_calls[0], ToolCall)
    assert got.steps[0].results[0].data == {"path": "a.txt"}
    assert got.to_dict() == t.to_dict()
    assert store.get_task("nope") is None


def test_save_task_upserts_and_bumps_updated_at():
    store = SQLiteTaskStore(":memory:")
    t = _task()
    store.save_task(t)
    first = t.updated_at
    time.sleep(0.01)
    t.status = TaskStatus.COMPLETED
    store.save_task(t)
    assert len(store.list_tasks()) == 1
    got = store.get_task(t.id)
    assert got.status is TaskStatus.COMPLETED and got.updated_at > first


def test_list_tasks_orders_newest_first_and_filters_status():
    store = SQLiteTaskStore(":memory:")
    a = _task("a", created_at=100.0)
    b = _task("b", created_at=300.0, status=TaskStatus.COMPLETED)
    c = _task("c", created_at=200.0)
    for t in (a, b, c):
        store.save_task(t)
    assert [t.title for t in store.list_tasks()] == ["b", "c", "a"]
    assert [t.title for t in store.list_tasks(limit=2)] == ["b", "c"]
    assert [t.title for t in store.list_tasks(status="completed")] == ["b"]
    assert [t.title for t in store.list_tasks(status=TaskStatus.PENDING)] == ["c", "a"]


def test_delete_task_cascades_to_plans_and_events():
    store = SQLiteTaskStore(":memory:")
    t = _task()
    store.save_task(t)
    plan = Plan(task_id=t.id, steps=[Step("s")])
    store.save_plan(plan)
    store.append_event(Event(kind=EventKind.LOG, message="x", task_id=t.id))
    store.delete_task(t.id)
    assert store.get_task(t.id) is None
    assert store.get_plan(plan.id) is None
    assert store.events(t.id) == []


def test_plan_roundtrip_and_plans_for_task():
    store = SQLiteTaskStore(":memory:")
    p1 = Plan(task_id="t1", steps=[Step("one"), Step("two")], rationale="why")
    p2 = Plan(task_id="t1", steps=[Step("three")])
    store.save_plan(p1)
    store.save_plan(p2)
    got = store.get_plan(p1.id)
    assert got is not None and [s.title for s in got.steps] == ["one", "two"]
    assert got.rationale == "why" and isinstance(got.steps[0], Step)
    assert [p.id for p in store.plans_for_task("t1")] == [p1.id, p2.id]


def test_attach_bus_captures_events_ordered_with_since():
    store = SQLiteTaskStore(":memory:")
    bus = EventBus()
    off = store.attach(bus)
    bus.emit(EventKind.TASK_CREATED, "created", task_id="t1")
    mid = time.time()
    time.sleep(0.005)
    bus.emit(EventKind.STEP_DONE, "step", task_id="t1", data={"index": 0})
    bus.emit(EventKind.LOG, "other task", task_id="t2")
    off()
    bus.emit(EventKind.LOG, "after unsubscribe", task_id="t1")

    evs = store.events("t1")
    assert [e.kind for e in evs] == [EventKind.TASK_CREATED, EventKind.STEP_DONE]
    assert evs[1].data == {"index": 0}
    assert [e.message for e in store.events("t1", since=mid)] == ["step"]
    assert [e.message for e in store.events("t1", limit=1)] == ["created"]
    assert [e.message for e in store.events("t2")] == ["other task"]


def test_append_event_is_idempotent():
    store = SQLiteTaskStore(":memory:")
    ev = Event(kind=EventKind.LOG, message="once", task_id="t")
    store.append_event(ev)
    store.append_event(ev)
    assert len(store.events("t")) == 1


def test_threaded_writes_from_many_publishers():
    store = SQLiteTaskStore(":memory:")
    bus = EventBus()
    store.attach(bus)
    n_threads, per_thread = 8, 50

    def work(i: int) -> None:
        for j in range(per_thread):
            bus.emit(EventKind.LOG, f"{i}-{j}", task_id="t")
            if j % 10 == 0:
                store.save_task(_task(f"task-{i}-{j}"))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(store.events("t", limit=10_000)) == n_threads * per_thread
    assert len(store.list_tasks(limit=10_000)) == n_threads * (per_thread // 10)


def test_agent_state_roundtrip_and_delete():
    store = SQLiteTaskStore(":memory:")
    st = AgentState(spec=AgentSpec(name="Builder", goal="g", tools=["create_file"]),
                    status=AgentStatus.RUNNING, current_task_id="t1", progress=0.5)
    store.save_agent(st)
    st.status = AgentStatus.IDLE
    store.save_agent(st)                 # upsert, not duplicate
    agents = store.list_agents()
    assert len(agents) == 1
    assert agents[0].id == st.id and agents[0].status is AgentStatus.IDLE
    assert isinstance(agents[0].spec, AgentSpec) and agents[0].spec.tools == ["create_file"]
    store.delete_agent(st.id)
    assert store.list_agents() == []


def test_file_store_persists_across_reopen(tmp_path):
    path = tmp_path / "nested" / "dir" / "agents.db"
    with SQLiteTaskStore(str(path)) as store:
        t = _task("persist me")
        store.save_task(t)
        store.append_event(Event(kind=EventKind.LOG, message="hello", task_id=t.id))
    assert path.exists()
    store2 = SQLiteTaskStore(str(path))
    try:
        assert [x.title for x in store2.list_tasks()] == ["persist me"]
        assert [e.message for e in store2.events(t.id)] == ["hello"]
    finally:
        store2.close()
