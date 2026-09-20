"""追加式事件存储测试。"""

from __future__ import annotations

from patchflow.domain.enums import EventActor, EventType
from patchflow.domain.events import AgentEvent
from patchflow.storage.event_store import JsonlEventStore


def test_event_store_preserves_append_order(tmp_path) -> None:
    """事件重新加载后必须保持原始顺序和 payload。"""

    store = JsonlEventStore(tmp_path / "trajectory.jsonl")
    first = AgentEvent(
        run_id="run-1",
        task_id="task-1",
        event_type=EventType.RUN_CREATED,
        actor=EventActor.SYSTEM,
        payload={"sequence": 1},
    )
    second = AgentEvent(
        run_id="run-1",
        task_id="task-1",
        event_type=EventType.PHASE_CHANGED,
        actor=EventActor.AGENT,
        causation_event_id=first.event_id,
        payload={"sequence": 2, "phase": "initialize"},
    )

    store.append(first)
    store.append(second)
    loaded = store.load_all()

    assert [event.event_id for event in loaded] == [first.event_id, second.event_id]
    assert loaded[1].payload["phase"] == "initialize"


def test_event_store_returns_empty_list_for_missing_file(tmp_path) -> None:
    """尚未产生事件时读取应返回空列表。"""

    store = JsonlEventStore(tmp_path / "missing.jsonl")

    assert store.load_all() == []

