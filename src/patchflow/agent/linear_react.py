"""可审计、可预算控制的最小线性 ReAct 基线。"""  # 说明本实现尚不是完整 PatchFlow 策略。

from __future__ import annotations  # 延迟解析类型标注。

import asyncio  # 用于全局墙钟硬超时。
import json  # 用于把结构化工具观察放入线性历史。
import time  # 用于统计单调墙钟时间。
from dataclasses import dataclass  # 保存不可变运行结果。
from datetime import UTC, datetime  # 保存 manifest 生命周期时间并统一使用 UTC。
from typing import Any  # 标注事件中的 JSON 兼容负载。

from patchflow.application.run_initializer import RunContext  # 复用运行目录和事件流。
from patchflow.domain.enums import (  # 复用状态与事件枚举。
    AgentPhase,  # 描述 Agent 当前阶段。
    EventActor,  # 描述事件生产者。
    EventType,  # 描述事件类别。
    RunStatus,  # 描述运行最终状态。
)  # 结束状态与事件导入。
from patchflow.domain.events import AgentEvent  # 构造追加式轨迹事件。
from patchflow.domain.runtime import Runtime  # 限定所有仓库操作通过运行时协议。
from patchflow.domain.task import TaskSpec  # 读取任务问题与预算。
from patchflow.domain.tools import Tool, ToolCall, ToolResult  # 复用受校验工具契约。
from patchflow.model.protocol import (  # 依赖 provider 无关模型协议。
    Model,  # 声明异步模型最小接口。
    ModelMessage,  # 构造可见线性历史。
    ModelRequest,  # 构造单次模型请求。
)  # 结束模型协议导入。
from patchflow.storage.manifest_store import ManifestStore  # 持久化运行终态。


@dataclass(frozen=True, slots=True)  # 禁止调用方修改已经结束的结果。
class AgentOutcome:  # 返回模型与运行时协作产生的最终结论。
    status: RunStatus  # 区分成功、任务失败和基础设施错误。
    stop_reason: str  # 保存机器可读终止原因。
    patch: str | None  # 仅在通过验证时保存完整补丁。


class LinearReactAgent:  # 实现没有候选分支和显式修复阶段的基线。
    def __init__(self, model: Model, tools: tuple[Tool, ...]) -> None:  # 注入模型和显式允许的工具。
        self._model = model  # 保留异步模型协议实例。
        self._tools = {tool.spec.name: tool for tool in tools}  # 使用稳定名称构造分发表。
        if len(self._tools) != len(tools):  # 阻止重复名称产生不确定路由。
            raise ValueError("工具名称不能重复")  # 让装配错误尽早暴露。

    async def run(self, task: TaskSpec, context: RunContext, runtime: Runtime) -> AgentOutcome:  # 执行单个任务。
        if context.state.task_id != task.task_id or context.manifest.task_id != task.task_id or context.manifest.run_id != context.state.run_id:  # 检查上下文归属。
            raise ValueError("运行上下文与任务不匹配")  # 防止轨迹与任务交叉写入。
        if context.state.phase is not AgentPhase.CREATED or context.manifest.status is not RunStatus.PENDING:  # 拒绝复用已经消费的运行上下文。
            raise ValueError("运行上下文必须处于初始状态")  # 防止覆盖前一次运行的 manifest 和补丁。
        self._task = task  # 保存本次运行的不可变任务。
        self._context = context  # 保存本次运行的状态和 artifact。
        self._runtime = runtime  # 保存经过显式注入的隔离后端。
        self._history = [ModelMessage("system", task.problem_statement)]  # 从 Issue 开始线性上下文。
        self._verified = False  # 禁止把修复前或过期测试结果视为最终验证。
        self._call_ids: set[str] = set()  # 防止模型重复使用调用 ID 污染轨迹。
        self._last_event_id = context.event_store.load_all()[-1].event_id  # 从初始化事件开始建立因果链。
        self._started_at = time.perf_counter()  # 记录含 Runtime 启动时间的全局起点。
        outcome = AgentOutcome(RunStatus.INFRASTRUCTURE_ERROR, "not_started", None)  # 默认以保守终态初始化。
        context.manifest.started_at = datetime.now(UTC)  # 记录本次运行实际启动时间。
        context.manifest.status = RunStatus.PREPARING  # 标记运行时尚未通过启动检查。
        ManifestStore(context.layout.manifest_path).save(context.manifest)  # 持久化准备状态。
        try:  # 确保超时和异常仍会清理运行时。
            outcome = await asyncio.wait_for(self._drive(), timeout=task.budget.max_wall_clock_seconds)  # 对整个任务施加硬时限。
        except TimeoutError:  # 全局时间预算耗尽时得到可解释结果。
            outcome = AgentOutcome(RunStatus.FAILED, "wall_clock_seconds", None)  # 不输出未验证补丁。
        except Exception as error:  # 将模型或环境异常记录为可审计基础设施错误。
            outcome = AgentOutcome(RunStatus.INFRASTRUCTURE_ERROR, type(error).__name__, None)  # 避免将异常误计作修复失败。
        finally:  # 无论结果如何都关闭运行时并更新清单。
            try:  # 单独处理容器销毁失败。
                await runtime.close()  # 释放进程、容器和临时资源。
            except Exception as error:  # 容器未能清理时不能报告成功。
                outcome = AgentOutcome(RunStatus.INFRASTRUCTURE_ERROR, f"runtime_close_{type(error).__name__}", None)  # 保守覆盖终态。
            context.state.usage.wall_clock_seconds = time.perf_counter() - self._started_at  # 记录真实总耗时。
            context.state.status = outcome.status  # 将结果反映在内存状态中。
            context.state.stop_reason = outcome.stop_reason  # 保存可供调用方读取的停止原因。
            terminal_phase = AgentPhase.COMPLETED if outcome.status is RunStatus.SUCCEEDED else AgentPhase.FAILED  # 选择基线终态。
            if context.state.phase is AgentPhase.LINEAR_REACT:  # 仅在成功进入基线阶段后迁移。
                context.state.transition_to(terminal_phase)  # 使用现有状态迁移校验。
            if outcome.patch is not None:  # 只有已验证的成功结果才生成最终文件。
                context.layout.final_patch_path.write_text(outcome.patch, encoding="utf-8")  # 保存可应用的完整 patch。
                context.manifest.final_patch_path = str(context.layout.final_patch_path)  # 记录 artifact 路径。
            context.manifest.status = outcome.status  # 保存正式运行状态。
            context.manifest.stop_reason = outcome.stop_reason  # 保存机器可读停止原因。
            context.manifest.finished_at = datetime.now(UTC)  # 保存终止时间。
            ManifestStore(context.layout.manifest_path).save(context.manifest)  # 原子持久化最终清单。
            event_type = EventType.RUN_COMPLETED if outcome.status is RunStatus.SUCCEEDED else EventType.RUN_FAILED  # 选择终止事件类型。
            self._record(event_type, EventActor.AGENT, {"status": outcome.status.value, "reason": outcome.stop_reason})  # 记录最终决定。
        return outcome  # 返回与磁盘清单一致的最终结果。

    async def _drive(self) -> AgentOutcome:  # 启动 Runtime 并进入线性决策循环。
        await self._runtime.start(self._task)  # 让 Runtime 检查基础提交和工作区安全。
        self._context.state.transition_to(AgentPhase.LINEAR_REACT)  # 进入独立于完整策略的基线阶段。
        self._context.state.status = RunStatus.RUNNING  # 标记任务已经开始执行。
        self._context.manifest.status = RunStatus.RUNNING  # 同步更新运行清单。
        ManifestStore(self._context.layout.manifest_path).save(self._context.manifest)  # 持久化运行中状态。
        self._record(EventType.PHASE_CHANGED, EventActor.AGENT, {"phase": AgentPhase.LINEAR_REACT.value})  # 记录阶段入口。
        while True:  # 每轮只请求一个工具动作或结束决策。
            usage = self._context.state.usage  # 读取当前累计预算消耗。
            reached = tuple(name for name in usage.exceeded_items(self._task.budget) if name != "tool_calls")  # 工具次数单独在调度前检查。
            if reached:  # 模型、步数、token、命令耗时或成本已到上限。
                return AgentOutcome(RunStatus.FAILED, reached[0], None)  # 停止后续模型请求。
            request = ModelRequest(self._task.task_id, self._task.problem_statement, tuple(self._history), tuple(tool.spec for tool in self._tools.values()))  # 仅发送 Agent 可见任务数据。
            self._record(EventType.MODEL_REQUESTED, EventActor.AGENT, {"history_length": len(request.history)})  # 记录请求边界。
            response = await self._model.complete(request)  # 从 FakeModel 或真实适配器取得下一步。
            usage.model_calls += 1  # 每次成功回复消耗一次模型调用。
            usage.agent_steps += 1  # 每次模型决策消耗一个 Agent 步骤。
            usage.input_tokens += response.usage.input_tokens  # 累加 provider 报告的输入 token。
            usage.output_tokens += response.usage.output_tokens  # 累加 provider 报告的输出 token。
            usage.cost_usd += response.usage.cost_usd  # 累加 provider 报告的成本。
            self._record(EventType.MODEL_RESPONDED, EventActor.MODEL, {"tool": response.tool_call.tool_name if response.tool_call else None, "final": response.final_answer, "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens})  # 保存决策与用量。
            budget = self._task.budget  # 读取本次任务的硬限制。
            if usage.input_tokens > budget.max_input_tokens or usage.output_tokens > budget.max_output_tokens:  # 阻止超量回复继续调用工具。
                return AgentOutcome(RunStatus.FAILED, "token_budget", None)  # 保留超支事实供轨迹分析。
            if budget.max_cost_usd is not None and usage.cost_usd > budget.max_cost_usd:  # 检查可选花费上限。
                return AgentOutcome(RunStatus.FAILED, "cost_usd", None)  # 停止一切进一步动作。
            if response.final_answer is not None:  # 模型决定结束当前线性轨迹。
                self._history.append(ModelMessage("assistant", response.final_answer))  # 保留最终模型说明。
                if not self._verified:  # 只有最新候选通过测试才可声称成功。
                    return AgentOutcome(RunStatus.FAILED, "unverified_candidate", None)  # 拒绝凭模型自述通过。
                patch = await self._runtime.get_diff()  # 直接由 Runtime 导出完整最终补丁。
                if not patch:  # 空工作区修改不能算修复成功。
                    return AgentOutcome(RunStatus.FAILED, "empty_patch", None)  # 明确失败原因。
                return AgentOutcome(RunStatus.SUCCEEDED, "verified_patch", patch)  # 返回经过公开测试的补丁。
            call = response.tool_call  # 缩小类型到工具调用分支。
            assert call is not None  # ModelResponse 已保证最终文本与工具调用恰有其一。
            if usage.tool_calls >= budget.max_tool_calls:  # 在执行前检查工具调用硬上限。
                return AgentOutcome(RunStatus.FAILED, "tool_calls", None)  # 不执行越界工具动作。
            usage.tool_calls += 1  # 未知工具和无效参数同样计入尝试次数。
            self._record(EventType.TOOL_REQUESTED, EventActor.AGENT, {"call_id": call.call_id, "name": call.tool_name, "arguments": call.arguments})  # 保存动作原文。
            result = await self._invoke(call)  # 分发到经过注入的工具集合。
            if call.tool_name in {"run_tests", "run_command"}:  # 仅把执行类工具计入命令耗时。
                usage.command_seconds += result.elapsed_seconds  # 累积包含超时的工具执行耗时。
            tool = self._tools.get(call.tool_name)  # 获取实际注册工具的权限声明。
            if tool is not None and not tool.spec.read_only:  # 任何当前或未来的写工具都使旧测试失效。
                self._verified = False  # 要求模型再次测试最新工作区。
            if call.tool_name == "run_tests":  # 测试反馈决定当前候选是否通过。
                self._verified = result.success  # 非零退出、超时与参数错误都不能通过验证。
            if not result.success:  # 保存最近失败原因供状态分析。
                self._context.state.last_failure = result.summary  # 更新 Agent 当前可恢复视图。
            self._record(EventType.TOOL_COMPLETED, EventActor.TOOL, {"call_id": result.call_id, "success": result.success, "error_type": result.error_type, "summary": result.summary, "data": result.data, "truncated": result.truncated})  # 记录完整结构化观察。
            observation = json.dumps({"call_id": result.call_id, "success": result.success, "summary": result.summary, "error_type": result.error_type, "data": result.data, "truncated": result.truncated}, ensure_ascii=False, default=str)  # 构造下一轮模型可见反馈。
            self._history.append(ModelMessage("assistant", f"调用 {call.tool_name}: {call.arguments}"))  # 保存产生观察的动作。
            self._history.append(ModelMessage("tool", observation))  # 保留成功或失败工具反馈。
            self._record(EventType.BUDGET_UPDATED, EventActor.AGENT, {"steps": usage.agent_steps, "model_calls": usage.model_calls, "tool_calls": usage.tool_calls, "command_seconds": usage.command_seconds})  # 记录本轮累计资源。

    async def _invoke(self, call: ToolCall) -> ToolResult:  # 将模型调用映射到已注册工具。
        if call.call_id in self._call_ids:  # 检查轨迹内调用 ID 唯一性。
            return ToolResult(call.call_id, call.tool_name, False, summary="工具调用 ID 重复。", error_type="duplicate_call_id")  # 反馈模型协议错误。
        self._call_ids.add(call.call_id)  # 在分发前占用调用 ID。
        tool = self._tools.get(call.tool_name)  # 只从显式工具注册表中查找。
        if tool is None:  # 未注册的名称绝不落到 Shell 或宿主执行。
            return ToolResult(call.call_id, call.tool_name, False, summary="未知工具。", error_type="unknown_tool")  # 返回可反思错误。
        return await tool.execute(call, self._runtime)  # 使用原有 Pydantic 校验和 Runtime 边界执行。

    def _record(self, event_type: EventType, actor: EventActor, payload: dict[str, Any]) -> None:  # 追加单条轨迹事件。
        event = AgentEvent(run_id=self._context.state.run_id, task_id=self._task.task_id, event_type=event_type, actor=actor, causation_event_id=self._last_event_id, payload=payload)  # 构造有因果父事件的轨迹。
        self._context.event_store.append(event)  # 立即刷新 JSONL 以便故障后回放。
        self._last_event_id = event.event_id  # 后续事件以本事件为因果父节点。
