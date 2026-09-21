"""Runtime 层使用的明确错误类型。"""  # 说明本文件集中定义运行时异常。


class PatchFlowRuntimeError(RuntimeError):  # 定义所有 PatchFlow 运行时异常的基类。
    """所有可识别 Runtime 错误的共同基类。"""  # 便于上层统一捕获运行时错误。


class RuntimeNotStartedError(PatchFlowRuntimeError):  # 定义生命周期使用错误。
    """在 Runtime 启动前调用了需要工作区的方法。"""  # 说明异常触发条件。


class WorkspaceSafetyError(PatchFlowRuntimeError):  # 定义隔离目录安全异常。
    """工作区不满足 LocalRuntime 的隔离要求。"""  # 说明异常触发条件。


class PathViolationError(PatchFlowRuntimeError):  # 定义文件路径策略异常。
    """请求路径越界、被禁止或命中只读规则。"""  # 说明异常触发条件。


class DirtyWorkspaceError(PatchFlowRuntimeError):  # 定义 Git 工作区状态异常。
    """任务启动时仓库包含未提交修改或未跟踪文件。"""  # 说明异常触发条件。

