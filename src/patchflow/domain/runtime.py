"""Runtime 协议与基础执行结果。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from patchflow.domain.task import TaskSpec


@dataclass(frozen=True, slots=True)
class CommandResult:
    """一次受控命令执行的完整结果。"""

    command: tuple[str, ...]
    return_code: int | None
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool = False
    output_truncated: bool = False
    termination_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        """只有正常退出且退出码为零才视为成功。"""

        return not self.timed_out and self.return_code == 0


@dataclass(frozen=True, slots=True)
class PatchResult:
    """一次补丁应用操作的结果。"""

    applied: bool
    changed_files: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    rejection_reason: str | None = None


@runtime_checkable
class Runtime(Protocol):
    """Agent 与执行环境之间唯一允许使用的接口。

    具体实现可以是本地临时目录、Docker 或 SWE-bench 容器。协议采用异步
    方法，便于后续在不阻塞 Agent 调度器的情况下管理命令和多个候选。
    """

    async def start(self, task: TaskSpec) -> None:
        """准备一个位于指定基础提交的干净任务环境。"""

    async def execute(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
    ) -> CommandResult:
        """在受控工作目录中执行不经过 Shell 拼接的命令。"""

    async def read_file(
        self,
        path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """读取工作区内经过路径校验的文件片段。"""

    async def apply_patch(self, patch: str) -> PatchResult:
        """在当前候选工作区应用统一 diff。"""

    async def get_diff(self) -> str:
        """返回相对于任务基础提交的标准 Git diff。"""

    async def reset(self) -> None:
        """丢弃当前可变修改并恢复任务初始状态。"""

    async def close(self) -> None:
        """释放进程、容器、临时文件和其他资源。"""

