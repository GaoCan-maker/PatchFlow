"""追加式 Agent 事件模型。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from patchflow.domain.enums import EventActor, EventType


def utc_now() -> datetime:
    """返回带 UTC 时区的时间，避免本地时区造成实验歧义。"""

    return datetime.now(timezone.utc)


class AgentEvent(BaseModel):
    """轨迹中的不可变事件。

    payload 只允许 JSON 可序列化值，确保事件能稳定写入 JSONL，并可被不同
    语言的轨迹工具读取。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    candidate_id: str | None = None
    event_type: EventType
    actor: EventActor
    schema_version: int = Field(default=1, ge=1)
    occurred_at: datetime = Field(default_factory=utc_now)
    monotonic_ns: int = Field(default_factory=time.monotonic_ns, ge=0)
    causation_event_id: str | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)

