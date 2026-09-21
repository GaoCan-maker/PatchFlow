"""一般命令与测试命令工具。"""  # 说明本文件只负责调用受控 Runtime。

from __future__ import annotations  # 启用延迟解析类型标注。

from pydantic import Field, field_validator  # 导入命令字段约束和内容校验器。

from patchflow.domain.enums import PermissionLevel  # 导入工具权限等级。
from patchflow.domain.runtime import CommandResult, Runtime  # 导入命令结果和运行时协议。
from patchflow.domain.tools import ToolSpec  # 导入工具声明模型。
from patchflow.tools.base import ToolArguments, ToolOutcome, ValidatedTool  # 导入工具基础设施。


class CommandArguments(ToolArguments):  # 定义不经过 Shell 的命令参数。
    """命令必须表示为参数数组，禁止把整条命令作为 Shell 字符串。"""  # 说明安全约束。

    command: tuple[str, ...] = Field(min_length=1, max_length=100)  # 限制参数个数并要求至少有可执行文件。
    timeout_seconds: float = Field(default=30.0, gt=0, le=1_800.0)  # 限制单次命令超时范围。

    @field_validator("command")  # 对完整参数数组执行内容检查。
    @classmethod  # 声明校验器不读取实例状态。
    def command_items_must_not_be_blank(cls, command: tuple[str, ...]) -> tuple[str, ...]:  # 定义非空参数规则。
        if any(not item for item in command):  # 检查可执行文件和每个参数是否为空字符串。
            raise ValueError("command 不能包含空参数")  # 拒绝语义不明确的命令数组。
        return command  # 返回通过检查的原始参数数组。


class RunTestsArguments(CommandArguments):  # 定义测试命令参数。
    """当前与一般命令共享结构，独立类型便于未来增加测试选择字段。"""  # 说明保留独立模型的原因。


def _command_outcome(result: CommandResult, *, operation: str) -> ToolOutcome:  # 定义命令结果到工具观察的转换。
    if result.timed_out:  # 优先识别超时而不是普通非零退出。
        return ToolOutcome(  # 构造命令超时观察。
            success=False,  # 标记操作未在预算内完成。
            data={  # 保存结构化进程观察。
                "command": list(result.command),  # 返回原始参数数组。
                "return_code": result.return_code,  # 返回被信号终止后的退出状态。
                "stdout": result.stdout,  # 返回超时前标准输出。
                "stderr": result.stderr,  # 返回超时前错误输出。
                "timed_out": True,  # 显式保存超时状态。
            },  # 完成结构化进程观察。
            summary=f"{operation}超时。",  # 提供 Agent 可读短摘要。
            error_type="command_timeout",  # 提供稳定错误类型。
            error_message=result.termination_reason or "timeout",  # 保存底层终止原因。
            truncated=result.output_truncated,  # 传播输出截断状态。
        )  # 完成超时观察。
    if not result.succeeded:  # 检查命令不存在或非零退出。
        error_type = result.termination_reason or "nonzero_exit"  # 优先使用进程启动失败等底层分类。
        return ToolOutcome(  # 构造命令失败观察。
            success=False,  # 标记操作失败。
            data={  # 保存结构化进程观察。
                "command": list(result.command),  # 返回原始参数数组。
                "return_code": result.return_code,  # 返回非零退出码或空退出码。
                "stdout": result.stdout,  # 返回标准输出。
                "stderr": result.stderr,  # 返回错误输出。
                "timed_out": False,  # 明确本次失败不是超时。
            },  # 完成结构化进程观察。
            summary=f"{operation}失败，退出码为 {result.return_code}。",  # 提供紧凑失败摘要。
            error_type=error_type,  # 保存机器可读错误分类。
            error_message=result.stderr or result.stdout,  # 返回最有诊断价值的输出。
            truncated=result.output_truncated,  # 传播输出截断状态。
        )  # 完成失败观察。
    return ToolOutcome(  # 构造命令成功观察。
        success=True,  # 标记操作成功。
        data={  # 保存结构化进程观察。
            "command": list(result.command),  # 返回原始参数数组。
            "return_code": result.return_code,  # 返回零退出码。
            "stdout": result.stdout,  # 返回标准输出。
            "stderr": result.stderr,  # 返回可能存在的非致命警告。
            "timed_out": False,  # 明确本次命令未超时。
        },  # 完成结构化进程观察。
        summary=f"{operation}成功。",  # 提供 Agent 可读成功摘要。
        truncated=result.output_truncated,  # 传播输出截断状态。
    )  # 完成成功观察。


class RunCommandTool(ValidatedTool[CommandArguments]):  # 定义一般受控命令工具。
    """执行参数数组形式的一般命令，不启用 Shell。"""  # 说明该工具仍属于高权限操作。

    def __init__(self) -> None:  # 构造稳定工具声明。
        super().__init__(  # 初始化共享校验工具基类。
            ToolSpec(  # 声明工具元数据。
                name="run_command",  # 设置模型调用名称。
                description="在固定工作区执行参数数组形式的命令，不经过 Shell。",  # 描述安全边界。
                permission=PermissionLevel.HIGH_RISK_EXECUTE,  # 标记该工具需要最高执行权限。
                read_only=False,  # 一般命令可能修改工作区。
                default_timeout_seconds=30.0,  # 设置默认命令时限。
                max_output_chars=20_000,  # 设置最大观察文本长度。
            ),  # 完成工具声明。
            CommandArguments,  # 绑定严格命令参数模型。
        )  # 完成基类初始化。

    async def _run(self, arguments: CommandArguments, runtime: Runtime) -> ToolOutcome:  # 执行一般命令语义。
        result = await runtime.execute(arguments.command, timeout_seconds=arguments.timeout_seconds)  # 委托 Runtime 执行。
        return _command_outcome(result, operation="命令执行")  # 转换为统一工具观察。


class RunTestsTool(ValidatedTool[RunTestsArguments]):  # 定义测试命令工具。
    """执行由任务或 Agent 选择的受控测试命令。"""  # 说明工具职责。

    def __init__(self) -> None:  # 构造稳定工具声明。
        super().__init__(  # 初始化共享校验工具基类。
            ToolSpec(  # 声明工具元数据。
                name="run_tests",  # 设置模型调用名称。
                description="运行参数数组形式的测试命令并返回退出码与输出。",  # 描述工具用途。
                permission=PermissionLevel.EXECUTE,  # 声明普通开发命令执行权限。
                read_only=True,  # 语义上测试不应修改产品代码，临时缓存由 Runtime 隔离。
                default_timeout_seconds=300.0,  # 为测试提供比普通命令更长的默认时限。
                max_output_chars=20_000,  # 设置测试输出上限。
            ),  # 完成工具声明。
            RunTestsArguments,  # 绑定独立测试参数模型。
        )  # 完成基类初始化。

    async def _run(self, arguments: RunTestsArguments, runtime: Runtime) -> ToolOutcome:  # 执行测试语义。
        result = await runtime.execute(arguments.command, timeout_seconds=arguments.timeout_seconds)  # 委托 Runtime 执行。
        return _command_outcome(result, operation="测试")  # 转换为统一测试观察。

