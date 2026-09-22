"""仅暴露受控 shell 命令的对照基线。"""  # 与细粒度工具的 ReAct 比较。

from __future__ import annotations  # 延迟解析类型标注。

import shlex  # 把公开测试命令转换为准确的参数数组。

from patchflow.agent.linear_react import AgentOutcome, LinearReactAgent  # 复用预算、轨迹和循环。
from patchflow.application.run_initializer import RunContext  # 标注初始化后的运行上下文。
from patchflow.domain.runtime import Runtime  # 标注运行时协议。
from patchflow.domain.task import TaskSpec  # 读取公开测试命令。
from patchflow.domain.tools import ToolCall, ToolResult  # 检查模型动作与执行结果。
from patchflow.runtime.docker import DockerRuntime  # 强制高风险命令只在 Docker 内执行。
from patchflow.tools.command import RunCommandTool  # 唯一允许暴露给模型的命令工具。


class BashOnlyAgent(LinearReactAgent):  # 在线性循环中限制模型工具空间。
    def __init__(self, model: object) -> None:  # 接收可替换的模型实现。
        super().__init__(model, (RunCommandTool(),))  # 仅公布通用命令，不公布细粒度读写或专用测试工具。

    async def run(self, task: TaskSpec, context: RunContext, runtime: Runtime) -> AgentOutcome:  # 先检查隔离级别再启动 Agent。
        if not isinstance(runtime, DockerRuntime):  # 任意模型命令不能落到本机 LocalRuntime。
            raise ValueError("Bash-only 策略必须使用 DockerRuntime")  # 拒绝不安全的运行配置。
        return await super().run(task, context, runtime)  # 使用共享运行生命周期和硬预算。

    def _is_verification_call(self, call: ToolCall, result: ToolResult) -> bool:  # 仅公开测试命令可验证最终候选。
        if call.tool_name != "run_command":  # 防御其他工具误被加入注册表。
            return False  # 不承认非通用命令为测试动作。
        raw = call.arguments.get("command")  # 读取未经 Shell 拼接的原始参数数组。
        if not isinstance(raw, (tuple, list)) or not all(isinstance(item, str) for item in raw):  # 拒绝无效结构。
            return False  # Pydantic 会单独反馈参数错误。
        allowed = {tuple(shlex.split(command)) for command in self._task.public_commands}  # 精确解析任务公开测试命令。
        return tuple(raw) in allowed  # 只承认与允许的公开测试 argv 完全一致的动作。
