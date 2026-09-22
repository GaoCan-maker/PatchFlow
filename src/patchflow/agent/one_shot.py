"""只看一次仓库快照、只生成一次补丁的对照基线。"""  # 不允许生成阶段使用测试反馈。

from __future__ import annotations  # 延迟解析类型标注。

from patchflow.agent.linear_react import (  # 复用运行生命周期与审计记录。
    AgentOutcome,  # 表示生成补丁或失败的结果。
    LinearReactAgent,  # 复用运行清单与预算生命周期。
)  # 结束公共 Agent 导入。
from patchflow.domain.enums import (  # 使用既有状态和事件枚举。
    AgentPhase,  # 记录单轮阶段。
    EventActor,  # 记录事件来源。
    EventType,  # 记录事件类型。
    RunStatus,  # 记录运行终态。
)  # 结束枚举导入。
from patchflow.model.protocol import ModelMessage, ModelRequest  # 构造无工具的单轮模型请求。
from patchflow.runtime.errors import (  # 识别禁止路径与过长文件片段。
    PathViolationError,  # 跳过任务路径策略禁止的仓库文件。
    WorkspaceSafetyError,  # 跳过超过读取上限的源码片段。
)  # 结束运行时错误导入。
from patchflow.storage.manifest_store import ManifestStore  # 持久化运行中的状态。


class OneShotAgent(LinearReactAgent):  # 复用共同的超时、异常和 artifact 生命周期。
    def __init__(self, model: object, *, snapshot_bytes: int = 24_000) -> None:  # 注入模型与固定仓库快照预算。
        if snapshot_bytes < 512:  # 避免快照预算小到无法容纳任何代码。
            raise ValueError("snapshot_bytes 不能小于 512")  # 在运行前暴露配置错误。
        super().__init__(model, ())  # 单轮基线绝不向模型公布工具。
        self._snapshot_bytes = snapshot_bytes  # 保存与任务无关的固定上下文额度。

    async def _drive(self) -> AgentOutcome:  # 在一次模型回复后尝试应用补丁。
        await self._runtime.start(self._task)  # 创建独立任务工作区。
        self._context.state.transition_to(AgentPhase.ONE_SHOT)  # 进入专属单轮阶段。
        self._context.state.status = RunStatus.RUNNING  # 更新内存运行状态。
        self._context.manifest.status = RunStatus.RUNNING  # 更新持久化运行状态。
        ManifestStore(self._context.layout.manifest_path).save(self._context.manifest)  # 刷新运行清单。
        self._record(EventType.PHASE_CHANGED, EventActor.AGENT, {"phase": AgentPhase.ONE_SHOT.value})  # 记录阶段入口。
        listing = await self._runtime.execute(("git", "ls-files", "-z", "--", "*.py"), timeout_seconds=30.0)  # 只枚举基础提交中的 Python 文件。
        if not listing.succeeded or listing.output_truncated:  # 截断文件表会破坏固定快照的可比性。
            return AgentOutcome(RunStatus.INFRASTRUCTURE_ERROR, "snapshot_listing", None)  # 拒绝不完整仓库索引。
        remaining = self._snapshot_bytes  # 记录本次可放入模型输入的源码字节数。
        fragments: list[str] = []  # 按路径顺序保存被选中的源码片段。
        for path in sorted(filter(None, listing.stdout.split("\x00"))):  # 固定排序以保证重复运行稳定。
            if path.startswith(".git/"):  # 不读取 Git 元数据目录。
                continue  # 保持路径策略边界。
            try:  # 路径策略可能拒绝仓库中的某些受保护文件。
                source = await self._runtime.read_file(path, start_line=1, end_line=120)  # 每文件最多读取前 120 行。
            except (PathViolationError, WorkspaceSafetyError):  # 仓库中可能存在禁止路径或超过读取上限的文件。
                continue  # 不把禁止路径作为模型上下文。
            fragment = f"\n### {path}\n{source}"  # 把文件名与内容绑定，避免源码归属模糊。
            size = len(fragment.encode("utf-8"))  # 使用 UTF-8 字节衡量快照规模。
            if size > remaining:  # 单个文件片段过大时跳过它而非截断代码。
                continue  # 保留后续更小文件进入快照的机会。
            fragments.append(fragment)  # 保存完整的已选源码片段。
            remaining -= size  # 更新剩余的固定快照预算。
        prompt = self._task.problem_statement + "\n请仅输出标准 Git unified diff 补丁，不要 Markdown 代码围栏。\n仓库快照：" + "".join(fragments)  # 告知模型输出契约与静态仓库上下文。
        context_slice = self._context_builder.build((ModelMessage("system", prompt),), ())  # 对完整输入执行请求容量预检。
        request = ModelRequest(self._task.task_id, self._task.problem_statement, context_slice.messages, ())  # 构造不含工具的单次请求。
        self._record(EventType.MODEL_REQUESTED, EventActor.AGENT, {"history_length": 1, "estimated_bytes": context_slice.estimated_bytes})  # 记录模型请求。
        response = await self._model.complete(request)  # 只允许发生一次模型调用。
        usage = self._context.state.usage  # 获取共享资源统计对象。
        usage.model_calls += 1  # 记录真实模型调用次数。
        usage.agent_steps += 1  # 记录单次生成决策。
        usage.input_tokens += response.usage.input_tokens  # 累加输入 token。
        usage.output_tokens += response.usage.output_tokens  # 累加输出 token。
        if response.usage.cost_usd is not None:  # 只有 provider 定价明确时累加成本。
            usage.cost_usd += response.usage.cost_usd  # 保存可计价金额。
        self._record(EventType.MODEL_RESPONDED, EventActor.MODEL, {"final": response.final_answer, "tool": response.tool_call.tool_name if response.tool_call else None, "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens, "cost_usd": response.usage.cost_usd})  # 审计原始单轮回复。
        if response.tool_call is not None:  # 单轮基线不得执行任何模型工具调用。
            return AgentOutcome(RunStatus.FAILED, "unexpected_tool_call", None)  # 拒绝越权动作。
        budget = self._task.budget  # 读取统一任务预算。
        if usage.input_tokens > budget.max_input_tokens or usage.output_tokens > budget.max_output_tokens:  # 阻止超额回复继续被应用。
            return AgentOutcome(RunStatus.FAILED, "token_budget", None)  # 保留超额事实。
        if budget.max_cost_usd is not None and response.usage.cost_usd is None:  # 有成本上限时禁止未知价格。
            return AgentOutcome(RunStatus.FAILED, "unpriced_model_usage", None)  # 不把未知成本当作零。
        if budget.max_cost_usd is not None and usage.cost_usd > budget.max_cost_usd:  # 检查真实已知成本。
            return AgentOutcome(RunStatus.FAILED, "cost_usd", None)  # 不应用超预算回复。
        patch = response.final_answer  # ModelResponse 保证最终文本非空。
        assert patch is not None  # 在类型层收窄最终文本。
        applied = await self._runtime.apply_patch(patch)  # 在独立工作区中做补丁语法和路径预检。
        self._record(EventType.PATCH_APPLIED, EventActor.RUNTIME, {"applied": applied.applied, "reason": applied.rejection_reason, "changed_files": list(applied.changed_files)})  # 把元组转为事件模型接受的 JSON 数组。
        if not applied.applied:  # 无法应用的输出不是候选补丁。
            return AgentOutcome(RunStatus.FAILED, "patch_rejected", None)  # 只返回机器可读失败原因。
        canonical = await self._runtime.get_diff()  # 导出由 Runtime 规范化的最终差异。
        if not canonical:  # 空差异没有修复价值。
            return AgentOutcome(RunStatus.FAILED, "empty_patch", None)  # 拒绝空补丁。
        return AgentOutcome(RunStatus.PATCH_GENERATED, "patch_generated", canonical)  # 留待独立评测器执行公开测试。
