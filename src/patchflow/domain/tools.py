"""工具声明、调用和结果协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from patchflow.domain.enums import PermissionLevel
from patchflow.domain.runtime import Runtime


class ToolSpec(BaseModel):
    """提供给模型与策略层的稳定工具元数据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    permission: PermissionLevel
    read_only: bool
    default_timeout_seconds: float = Field(default=30.0, gt=0)
    max_output_chars: int = Field(default=20_000, ge=1)


class ToolCall(BaseModel):
    """经过模型解析后、等待工具执行的结构化请求。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具执行完成后返回给 Agent 的统一观察。"""

    call_id: str
    tool_name: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    error_type: str | None = None
    error_message: str | None = None
    elapsed_seconds: float = 0.0
    truncated: bool = False


@runtime_checkable
class Tool(Protocol):
    """所有结构化工具必须实现的最小协议。"""

    @property
    def spec(self) -> ToolSpec:
        """返回工具稳定元数据和安全属性。"""

    async def execute(self, call: ToolCall, runtime: Runtime) -> ToolResult:
        """在显式 Runtime 中执行工具，禁止绕过 Runtime 访问宿主机。"""

