"""追加式 JSONL 事件存储。"""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from patchflow.domain.events import AgentEvent


class JsonlEventStore:
    """以一行一个 JSON 对象的形式持久化不可变事件。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: AgentEvent) -> None:
        """追加一个事件并立即刷新，降低异常退出时的轨迹损失。"""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialized = event.model_dump_json()
        with self._lock, self._path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.write("\n")
            stream.flush()

    def load_all(self) -> list[AgentEvent]:
        """按写入顺序读取全部事件。"""

        if not self._path.exists():
            return []
        events: list[AgentEvent] = []
        with self._path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    events.append(AgentEvent.model_validate_json(line))
                except ValueError as error:
                    raise ValueError(f"轨迹第 {line_number} 行不是合法事件") from error
        return events

