"""内置工具共享的参数校验和结果包装。"""  # 说明本文件减少各工具的重复错误处理。

from __future__ import annotations  # 启用延迟解析类型标注。

import time  # 导入单调计时能力。
from abc import ABC, abstractmethod  # 导入抽象工具基类能力。
from dataclasses import dataclass, field  # 导入轻量结果数据类。
from typing import Any, Generic, TypeVar  # 导入泛型工具参数所需类型。

from pydantic import BaseModel, ConfigDict, ValidationError  # 导入严格参数模型和校验异常。

from patchflow.domain.runtime import Runtime  # 导入运行时协议。
from patchflow.domain.tools import ToolCall, ToolResult, ToolSpec  # 导入稳定工具领域类型。
from patchflow.runtime.errors import PatchFlowRuntimeError  # 导入可安全反馈的运行时异常基类。


class ToolArguments(BaseModel):  # 定义所有工具参数模型的严格基类。
    """拒绝模型生成的未知参数，避免拼写错误被静默忽略。"""  # 解释严格配置的意义。

    model_config = ConfigDict(extra="forbid", frozen=True)  # 开启未知字段拒绝和不可变语义。


ArgumentsT = TypeVar("ArgumentsT", bound=ToolArguments)  # 声明工具参数模型泛型变量。


@dataclass(frozen=True, slots=True)  # 使用不可变紧凑对象表示工具内部执行结果。
class ToolOutcome:  # 定义不含调用元数据的内部工具结果。
    """由具体工具返回，再由基类补齐调用 ID 和耗时。"""  # 说明该类型只在工具层内部使用。

    success: bool  # 表示工具语义是否成功。
    data: dict[str, Any] = field(default_factory=dict)  # 保存结构化观察数据。
    summary: str = ""  # 保存适合直接反馈给 Agent 的短摘要。
    error_type: str | None = None  # 保存稳定机器可读错误类型。
    error_message: str | None = None  # 保存适合开发者诊断的错误文本。
    truncated: bool = False  # 表示观察是否因输出上限被截断。


class ValidatedTool(ABC, Generic[ArgumentsT]):  # 定义带统一校验和错误包装的工具基类。
    """把参数错误与 Runtime 错误统一转换为 ToolResult。"""  # 说明基类职责。

    def __init__(self, spec: ToolSpec, arguments_model: type[ArgumentsT]) -> None:  # 定义工具元数据构造函数。
        self._spec = spec  # 保存不可变工具声明。
        self._arguments_model = arguments_model  # 保存对应的 Pydantic 参数模型类型。

    @property  # 按 Tool 协议暴露只读元数据。
    def spec(self) -> ToolSpec:  # 定义工具声明属性。
        """返回稳定工具声明。"""  # 说明属性语义。

        return self._spec  # 返回构造时保存的不可变声明。

    async def execute(self, call: ToolCall, runtime: Runtime) -> ToolResult:  # 实现统一工具执行入口。
        """验证名称和参数，执行工具并归一化错误。"""  # 说明执行顺序。

        started_at = time.perf_counter()  # 记录工具层总耗时起点。
        if call.tool_name != self.spec.name:  # 检查调用名称是否与当前工具一致。
            return self._build_result(  # 构造工具分发错误结果。
                call=call,  # 保留原始调用 ID 和名称。
                started_at=started_at,  # 使用统一计时起点。
                outcome=ToolOutcome(  # 描述工具名称不匹配错误。
                    success=False,  # 标记调用未执行。
                    error_type="tool_name_mismatch",  # 提供稳定错误类型。
                    error_message=f"期望工具 {self.spec.name}，实际收到 {call.tool_name}",  # 提供诊断文本。
                    summary="工具名称不匹配，调用未执行。",  # 提供 Agent 可读短摘要。
                ),  # 完成名称错误描述。
            )  # 返回名称错误结果。
        try:  # 捕获 Pydantic 参数结构错误。
            arguments = self._arguments_model.model_validate(call.arguments)  # 严格解析模型生成参数。
        except ValidationError as error:  # 捕获缺失、类型错误和未知字段。
            return self._build_result(  # 构造参数校验失败结果。
                call=call,  # 保留原始调用元数据。
                started_at=started_at,  # 使用统一计时起点。
                outcome=ToolOutcome(  # 描述参数校验错误。
                    success=False,  # 标记工具主体未运行。
                    error_type="invalid_arguments",  # 提供稳定错误分类。
                    error_message=str(error),  # 保存 Pydantic 详细校验信息。
                    summary="工具参数无效，请根据参数模式重新调用。",  # 提供 Agent 修正方向。
                ),  # 完成参数错误描述。
            )  # 返回参数错误结果。
        try:  # 捕获路径、生命周期等可预期 Runtime 错误。
            outcome = await self._run(arguments, runtime)  # 委托具体工具实现语义操作。
        except PatchFlowRuntimeError as error:  # 捕获运行时安全和状态异常。
            outcome = ToolOutcome(  # 把异常转换为结构化失败观察。
                success=False,  # 标记工具操作失败。
                error_type=type(error).__name__,  # 使用异常类名保留精确分类。
                error_message=str(error),  # 保存具体错误原因。
                summary="Runtime 拒绝或无法完成该工具操作。",  # 提供稳定短摘要。
            )  # 完成运行时失败结果。
        except (OSError, UnicodeError, ValueError) as error:  # 捕获文件编码和参数边界等预期错误。
            outcome = ToolOutcome(  # 把基础错误转换为结构化失败观察。
                success=False,  # 标记工具操作失败。
                error_type=type(error).__name__,  # 保留具体错误类别。
                error_message=str(error),  # 保存开发者可诊断信息。
                summary="工具执行时遇到可恢复错误。",  # 提供 Agent 可读短摘要。
            )  # 完成基础错误结果。
        return self._build_result(call=call, started_at=started_at, outcome=outcome)  # 补齐耗时并返回最终结果。

    @abstractmethod  # 要求每个具体工具提供自己的核心逻辑。
    async def _run(self, arguments: ArgumentsT, runtime: Runtime) -> ToolOutcome:  # 声明工具核心执行接口。
        """执行已经通过严格校验的工具参数。"""  # 说明子类无需重复解析参数。

    @staticmethod  # 声明结果包装不依赖具体工具实例。
    def _build_result(  # 定义内部结果到领域结果的转换方法。
        *,  # 强制所有字段使用关键字传递以避免位置混淆。
        call: ToolCall,  # 接收原始工具调用。
        started_at: float,  # 接收工具开始的单调时钟值。
        outcome: ToolOutcome,  # 接收具体工具或错误处理产生的内部结果。
    ) -> ToolResult:  # 返回领域层统一工具结果。
        return ToolResult(  # 构造最终不可变 ToolResult。
            call_id=call.call_id,  # 回传调用 ID 供轨迹关联。
            tool_name=call.tool_name,  # 回传模型请求的工具名称。
            success=outcome.success,  # 复制工具语义成功状态。
            data=outcome.data,  # 复制结构化观察数据。
            summary=outcome.summary,  # 复制 Agent 可读摘要。
            error_type=outcome.error_type,  # 复制机器可读错误类型。
            error_message=outcome.error_message,  # 复制详细错误信息。
            elapsed_seconds=time.perf_counter() - started_at,  # 计算包含校验在内的工具总耗时。
            truncated=outcome.truncated,  # 复制输出截断标志。
        )  # 完成统一工具结果。

