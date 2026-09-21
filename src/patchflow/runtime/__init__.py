"""PatchFlow 运行时实现。"""  # 说明当前包负责提供受控执行环境。

from patchflow.runtime.errors import (  # 导出调用方需要识别的运行时异常。
    DirtyWorkspaceError,  # 导出工作区不干净异常。
    PathViolationError,  # 导出路径策略违规异常。
    RuntimeNotStartedError,  # 导出运行时尚未启动异常。
    WorkspaceSafetyError,  # 导出隔离工作区安全异常。
)  # 结束异常导入列表。
from patchflow.runtime.local import LocalRuntime  # 导出本地隔离运行时实现。
from patchflow.runtime.output import truncate_text  # 导出统一输出截断函数。
from patchflow.runtime.paths import WorkspacePathResolver  # 导出安全路径解析器。

__all__ = [  # 明确该包稳定暴露的公共名称。
    "DirtyWorkspaceError",  # 暴露工作区状态异常。
    "LocalRuntime",  # 暴露本地运行时。
    "PathViolationError",  # 暴露路径违规异常。
    "RuntimeNotStartedError",  # 暴露生命周期异常。
    "WorkspacePathResolver",  # 暴露路径解析器。
    "WorkspaceSafetyError",  # 暴露工作区安全异常。
    "truncate_text",  # 暴露输出截断函数。
]  # 结束公共名称列表。

