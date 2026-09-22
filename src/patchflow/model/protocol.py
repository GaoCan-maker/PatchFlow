"""与模型服务提供商无关的单步决策协议。"""  # 说明本文件不绑定任何 API SDK。

from __future__ import annotations  # 延迟解析本模块的类型标注。

from dataclasses import dataclass, field  # 使用不可变数据类保存请求与回复。
from typing import Protocol, runtime_checkable  # 使用结构化协议隔离具体模型。

from patchflow.domain.tools import ToolCall, ToolSpec  # 复用现有工具调用和声明。


@dataclass(frozen=True, slots=True)  # 保证消息在一次决策期间不可变。
class ModelMessage:  # 定义线性上下文的一条消息。
    role: str  # 标识 system、assistant 或 tool 等消息来源。
    content: str  # 保存该来源的可见文本内容。
    tool_call: ToolCall | None = None  # 助手消息可携带原始结构化函数调用。
    tool_call_id: str | None = None  # 工具消息保留与函数调用配对的原始 ID。


@dataclass(frozen=True, slots=True)  # 保证输入历史不会被模型适配器修改。
class ModelRequest:  # 定义模型完成一次 Agent 决策所需的输入。
    task_id: str  # 只公开任务标识，不传递仅评测层可见的字段。
    problem_statement: str  # 只公开 Agent 被允许看到的 Issue 描述。
    history: tuple[ModelMessage, ...]  # 保存按顺序追加的模型可见历史。
    tools: tuple[ToolSpec, ...]  # 声明本次允许模型调用的工具集合。


@dataclass(frozen=True, slots=True)  # 使 token 统计可以安全附着在模型回复上。
class ModelUsage:  # 定义 provider 应报告的资源用量。
    input_tokens: int = 0  # 保存输入 token 数；FakeModel 可返回零。
    output_tokens: int = 0  # 保存输出 token 数；真实适配器需填写实际值。
    cost_usd: float | None = None  # 未配置价格时保持未知，不能伪装成零成本。
    cached_input_tokens: int = 0  # 保存 provider 报告的缓存输入 token 数。

    def __post_init__(self) -> None:  # 防止错误计量污染全局预算。
        if self.input_tokens < 0 or self.output_tokens < 0 or self.cached_input_tokens < 0 or (self.cost_usd is not None and self.cost_usd < 0):  # 检查所有已知用量均非负。
            raise ValueError("模型用量不能为负数")  # 明确拒绝无效 provider 回复。


@dataclass(frozen=True, slots=True)  # 使一次模型决策只有一个稳定结果。
class ModelResponse:  # 表示一个工具动作或一个结束决策。
    tool_call: ToolCall | None = None  # 保存可选的结构化工具调用。
    final_answer: str | None = None  # 保存可选的结束说明。
    usage: ModelUsage = field(default_factory=ModelUsage)  # 保存本次模型资源用量。

    def __post_init__(self) -> None:  # 校验动作与结束说明互斥且必须存在其一。
        if (self.tool_call is None) == (self.final_answer is None):  # 排除零动作或双动作回复。
            raise ValueError("模型回复必须且只能包含工具调用或结束说明")  # 拒绝模糊决策。
        if self.final_answer is not None and not self.final_answer.strip():  # 拒绝只有空白的结束说明。
            raise ValueError("结束说明不能为空")  # 防止空文本误触发成功。


@runtime_checkable  # 允许测试和装配阶段检查协议符合性。
class Model(Protocol):  # 定义 Agent 依赖的最小异步模型接口。
    async def complete(self, request: ModelRequest) -> ModelResponse:  # 请求模型给出下一步动作。
        """返回一个工具动作或结束决策。"""  # 说明单次回复的边界。
