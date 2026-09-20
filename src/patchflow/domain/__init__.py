"""领域模型与稳定协议。

领域层只描述 PatchFlow 的业务概念，不依赖具体模型 SDK、Docker、CLI 或
SWE-bench，从而让不同基础设施实现可以共享同一套契约。
"""

from patchflow.domain.enums import (
    AgentPhase,
    EventActor,
    EventType,
    PermissionLevel,
    RepositoryKind,
    RunStatus,
    TaskSource,
)
from patchflow.domain.events import AgentEvent
from patchflow.domain.run import RunManifest
from patchflow.domain.runtime import CommandResult, PatchResult, Runtime
from patchflow.domain.state import AgentState, BudgetUsage, InvalidStateTransitionError
from patchflow.domain.task import Budget, PathPolicy, RepositorySpec, TaskSpec
from patchflow.domain.tools import Tool, ToolCall, ToolResult, ToolSpec

__all__ = [
    "AgentEvent",
    "AgentPhase",
    "AgentState",
    "Budget",
    "BudgetUsage",
    "CommandResult",
    "EventActor",
    "EventType",
    "InvalidStateTransitionError",
    "PatchResult",
    "PathPolicy",
    "PermissionLevel",
    "RepositoryKind",
    "RepositorySpec",
    "RunManifest",
    "RunStatus",
    "Runtime",
    "TaskSource",
    "TaskSpec",
    "Tool",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
]

