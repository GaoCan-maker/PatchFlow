"""第五周单候选自主策略：显式阶段、证据图与结构化失败反思。"""  # 第六周再扩展独立候选分支和验证金字塔。

from __future__ import annotations  # 延迟解析跨模块类型。

import asyncio  # 为完整任务施加墙钟硬超时。
import hashlib  # 对候选补丁做稳定重复检测。
import json  # 解析严格的模型阶段 JSON 回复。
import re  # 从公开测试输出提取失败测试名和栈帧文本。
import shlex  # 将公开测试命令解析为不经 Shell 的参数数组。
import time  # 累积任务墙钟和命令耗时。
from datetime import UTC, datetime  # 为运行清单记录 UTC 生命周期时间。
from typing import TypeVar  # 标注阶段决策模型泛型。

from pydantic import ValidationError  # 区分模型 JSON 格式错误。

from patchflow.agent.context import ContextTooLargeError  # 复用安全的上下文超限分类。
from patchflow.agent.decisions import (  # 导入四个阶段的严格决策模型。
    IssueUnderstanding,  # 校验 Issue 分解和复现计划。
    PatchProposal,  # 校验统一 diff 候选。
    ReflectionAction,  # 限定反思后的阶段路由。
    ReflectionDecision,  # 校验失败分类和证据更新。
    RepairPlan,  # 校验可证伪的修复计划。
)  # 完成阶段输出协议导入。
from patchflow.agent.linear_react import AgentOutcome  # 与现有评测返回值保持一致。
from patchflow.application.run_initializer import RunContext  # 使用现有 manifest、事件与 artifact。
from patchflow.config.models import AgentConfig  # 读取最大反思轮次配置。
from patchflow.domain.enums import (  # 使用正式状态与事件枚举。
    AgentPhase,  # 控制合法修复阶段迁移。
    EventActor,  # 标记轨迹事件生产者。
    EventType,  # 限定可回放事件类别。
    RunStatus,  # 标记任务最终状态。
)  # 完成状态和事件类型导入。
from patchflow.domain.events import AgentEvent  # 写入可回放的因果事件。
from patchflow.domain.runtime import CommandResult, Runtime  # 仅通过隔离 Runtime 操作仓库。
from patchflow.domain.task import TaskSpec  # 读取 Issue、公开命令和预算。
from patchflow.localization import (  # 复用第四周基础提交索引与定位器。
    RepoIndex,  # 标注已建立的仓库索引。
    build_repo_index,  # 从干净基础提交构建索引。
    localize,  # 组合 Issue、代码与失败证据定位。
)  # 完成定位模块导入。
from patchflow.memory.context import EvidenceContextBuilder  # 按阶段构造分区上下文。
from patchflow.memory.evidence import (  # 区分观测事实、推断和验证关系。
    EvidenceGraph,  # 保存任务级工作记忆。
    EvidenceKind,  # 标记节点业务类型。
    EvidenceOrigin,  # 保留来源可信度。
    EvidenceRelation,  # 创建证据因果边。
    HypothesisStatus,  # 管理根因假设状态。
)  # 完成证据图导入。
from patchflow.model.errors import ModelOutputError, ModelProviderError  # 分类模型错误。
from patchflow.model.protocol import Model, ModelRequest  # 使用 provider 无关模型接口。
from patchflow.storage.manifest_store import ManifestStore  # 原子保存任务清单。

DecisionT = TypeVar("DecisionT", IssueUnderstanding, RepairPlan, PatchProposal, ReflectionDecision)  # 声明四类结构化模型回复。
_FAILED_TEST = re.compile(r"(?:FAILED|ERROR)\s+([^\s]+::[^\s]+)")  # 提取 pytest 修复前或候选后的失败测试标识。
_FRAME = re.compile(r'File "([^"]+\.py)", line (\d+)')  # 提取标准 Python traceback 中的文件和行号。


class StrategyStop(Exception):  # 表示可解释的预算或策略终止。
    def __init__(self, reason: str) -> None:  # 保存机器可读停止原因。
        self.reason = reason  # 不携带敏感输出或 API 密钥。
        super().__init__(reason)  # 初始化标准异常消息。


class RuntimeEnvironmentFailure(Exception):  # 区分测试环境未启动与代码断言失败。
    def __init__(self, reason: str) -> None:  # 保存稳定环境错误分类。
        self.reason = reason  # 避免在终态中泄漏底层系统路径。
        super().__init__(reason)  # 初始化标准异常文本。


class PatchFlowAgent:  # 与第三周线性基线并列的显式阶段主策略。
    def __init__(self, model: Model, *, config: AgentConfig | None = None, context_builder: EvidenceContextBuilder | None = None) -> None:  # 注入模型、策略上限和上下文配额。
        self._model = model  # 保留 provider 无关模型实例。
        self._config = config or AgentConfig(strategy="patchflow")  # 默认选择第五周主策略配置。
        if self._config.max_candidates_per_round != 1:  # 第六周之前不伪装成多候选搜索。
            raise ValueError("第五周策略只支持每轮一个候选补丁")  # 明确单候选实现边界。
        self._context_builder = context_builder or EvidenceContextBuilder()  # 构造结构化分区上下文。

    def _record(self, event_type: EventType, actor: EventActor, payload: dict[str, object]) -> str:  # 追加带因果链的正式事件。
        event = AgentEvent(run_id=self._context.state.run_id, task_id=self._task.task_id, event_type=event_type, actor=actor, causation_event_id=self._last_event_id, payload=payload)  # 构造 JSON 可序列化事件。
        self._context.event_store.append(event)  # 立即刷入运行轨迹。
        self._last_event_id = event.event_id  # 让下一事件引用当前事件。
        return event.event_id  # 返回来源 ID 供证据节点引用。

    def _phase(self, phase: AgentPhase) -> None:  # 通过正式迁移表进入下一阶段。
        self._context.state.transition_to(phase)  # 拒绝跳过不合法阶段。
        self._record(EventType.PHASE_CHANGED, EventActor.AGENT, {"phase": phase.value})  # 保存可回放的阶段入口。

    def _sync_graph(self) -> None:  # 把图变更与当前状态版本一同持久化。
        self._graph.save(self._context.layout.run_dir / "evidence_graph.json")  # 原子保存完整工作记忆投影。
        self._context.state.evidence_graph_version = self._graph.version  # 更新可恢复当前状态的图版本。
        self._record(EventType.EVIDENCE_UPDATED, EventActor.AGENT, {"version": self._graph.version, "nodes": len(self._graph.nodes), "edges": len(self._graph.edges)})  # 记录本次快照边界。

    def _budget(self) -> None:  # 在模型和命令动作之前检查全局硬上限。
        usage = self._context.state.usage  # 读取本次运行累计资源。
        usage.wall_clock_seconds = time.perf_counter() - self._started_at  # 刷新实际墙钟时间。
        exceeded = usage.exceeded_items(self._task.budget)  # 使用项目既有预算比较逻辑。
        if exceeded:  # 任一资源到顶都不再产生新动作。
            raise StrategyStop(exceeded[0])  # 返回稳定预算原因。

    async def _ask(self, phase: AgentPhase, instruction: str, schema: type[DecisionT]) -> tuple[DecisionT, str]:  # 请求并验证一次阶段决策。
        self._budget()  # 在请求付费模型前检查全局预算。
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))  # 将严格输出字段描述交给模型。
        projection = self._context_builder.build(issue=self._task.problem_statement, phase=phase, instruction=f"{instruction}\n只返回符合此 JSON Schema 的对象：{schema_text}", graph=self._graph, modified_files=tuple(sorted(self._modified_files)))  # 构造不遗漏关键失败的模型上下文。
        self._record(EventType.CONTEXT_COMPACTED, EventActor.AGENT, {"phase": phase.value, "selected": list(projection.selected_node_ids), "dropped": list(projection.dropped_node_ids), "partition_bytes": projection.partition_bytes, "estimated_bytes": projection.estimated_bytes})  # 审计选入和丢弃的证据。
        request = ModelRequest(self._task.task_id, self._task.problem_statement, projection.messages, ())  # 此阶段只要求严格 JSON 而不公开任意 Shell 工具。
        self._record(EventType.MODEL_REQUESTED, EventActor.AGENT, {"phase": phase.value, "estimated_bytes": projection.estimated_bytes})  # 保存模型请求元数据。
        response = await self._model.complete(request)  # 恰好执行一次 provider 请求。
        usage = self._context.state.usage  # 读取可变的累计用量。
        usage.model_calls += 1  # 记录一次已返回的模型调用。
        usage.agent_steps += 1  # 记录一次模型决策步骤。
        usage.input_tokens += response.usage.input_tokens  # 累加 provider 报告的输入 token。
        usage.output_tokens += response.usage.output_tokens  # 累加 provider 报告的输出 token。
        if response.usage.cost_usd is not None:  # 只累加真实已知的模型成本。
            usage.cost_usd += response.usage.cost_usd  # 保存累计美元费用。
        event_id = self._record(EventType.MODEL_RESPONDED, EventActor.MODEL, {"phase": phase.value, "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens, "cost_usd": response.usage.cost_usd, "tool_call": response.tool_call.tool_name if response.tool_call else None})  # 保存回复用量和动作类型。
        budget = self._task.budget  # 读取当前任务的硬限制。
        if usage.input_tokens > budget.max_input_tokens or usage.output_tokens > budget.max_output_tokens:  # 超量回复不能被继续消费。
            raise StrategyStop("token_budget")  # 停止后续候选或测试操作。
        if budget.max_cost_usd is not None and response.usage.cost_usd is None:  # 显式成本上限不能把未知价格当零。
            raise StrategyStop("unpriced_model_usage")  # 安全停止本次任务。
        if budget.max_cost_usd is not None and usage.cost_usd > budget.max_cost_usd:  # 检查本次回复是否超出成本上限。
            raise StrategyStop("cost_usd")  # 停止后续动作。
        if response.final_answer is None or response.tool_call is not None:  # 阶段协议只接受纯 JSON 文本。
            raise ModelOutputError(f"{phase.value} 阶段必须返回 JSON 文本")  # 不执行模型误发的工具调用。
        try:  # 使用 Pydantic 对模型输出做严格结构化校验。
            decision = schema.model_validate_json(response.final_answer)  # 解析预期阶段类型。
        except (ValidationError, ValueError) as error:  # 缺字段、未知字段或类型错误均不可继续。
            raise ModelOutputError(f"{phase.value} 阶段的 JSON 不符合输出协议") from error  # 不将原文当作可信事实。
        return decision, event_id  # 交回经过类型校验的阶段决策与来源事件。

    async def _execute_test(self, command_text: str, *, candidate_id: str | None = None) -> CommandResult:  # 执行 TaskSpec 明确提供的公开测试。
        self._budget()  # 命令执行前检查剩余资源。
        if self._context.state.usage.tool_calls >= self._task.budget.max_tool_calls:  # 将测试与补丁视为受控工具动作。
            raise StrategyStop("tool_calls")  # 拒绝越过工具次数上限。
        try:  # 只解析参数数组，不交给 Shell。
            command = tuple(shlex.split(command_text))  # 转换公开测试文本为安全 argv。
        except ValueError as error:  # 不完整引号是任务数据错误。
            raise StrategyStop("invalid_public_command") from error  # 避免构造模糊命令。
        if not command:  # 空命令不能被执行。
            raise StrategyStop("invalid_public_command")  # 保留明确失败分类。
        remaining = self._task.budget.max_command_seconds - self._context.state.usage.command_seconds  # 计算命令总时间余量。
        if remaining <= 0:  # 总命令时间已耗尽。
            raise StrategyStop("command_seconds")  # 不再启动新的测试进程。
        self._context.state.usage.tool_calls += 1  # 先计入动作尝试次数。
        request_id = self._record(EventType.TOOL_REQUESTED, EventActor.AGENT, {"name": "run_tests", "command": list(command), "candidate_id": candidate_id})  # 审计实际执行的公开命令。
        result = await self._runtime.execute(command, timeout_seconds=min(300.0, remaining))  # 只在受控工作区执行并限制耗时。
        self._context.state.usage.command_seconds += result.elapsed_seconds  # 累计真实命令耗时。
        event_id = self._record(EventType.TOOL_COMPLETED, EventActor.TOOL, {"request_id": request_id, "name": "run_tests", "candidate_id": candidate_id, "return_code": result.return_code, "timed_out": result.timed_out, "stdout": result.stdout, "stderr": result.stderr, "truncated": result.output_truncated})  # 保存公开测试原始观察。
        test_node = self._graph.add_node(EvidenceKind.TEST, command_text, EvidenceOrigin.RUNTIME, f"event:{event_id}", metadata={"candidate_id": candidate_id, "return_code": result.return_code})  # 记录测试执行事实。
        if not result.succeeded:  # 失败或超时需要显式失败节点。
            detail = (result.stderr or result.stdout or result.termination_reason or "测试未成功")[-1_800:]  # 从有限输出尾部保留最有用的诊断。
            failure = self._graph.add_node(EvidenceKind.FAILURE, detail, EvidenceOrigin.RUNTIME, f"event:{event_id}", metadata={"candidate_id": candidate_id, "return_code": result.return_code, "timed_out": result.timed_out})  # 保存实际失败而非模型推断。
            self._graph.add_edge(test_node.node_id, failure.node_id, EvidenceRelation.FAILS_AT, f"event:{event_id}")  # 将具体失败归属到测试。
            for path, line in _FRAME.findall(f"{result.stdout}\n{result.stderr}")[:10]:  # 最多保存十个真实 Python 栈帧。
                frame = self._graph.add_node(EvidenceKind.STACK_FRAME, f"{path}:{line}", EvidenceOrigin.RUNTIME, f"event:{event_id}", metadata={"path": path, "line": int(line), "candidate_id": candidate_id})  # 将实际栈帧单独保存并引用测试事件。
                self._graph.add_edge(failure.node_id, frame.node_id, EvidenceRelation.FAILS_AT, f"event:{event_id}")  # 关联错误与精确代码位置。
            self._context.state.last_failure = detail[:500]  # 更新运行状态中最近失败摘要。
            self._last_test_output = f"{result.stdout}\n{result.stderr}"  # 保留完整受限输出供下一次定位使用。
            self._failed_tests = tuple(dict.fromkeys(_FAILED_TEST.findall(self._last_test_output)))  # 提取可用于导入关系的失败测试。
        self._sync_graph()  # 在每次真实测试后持久化图与版本。
        if result.return_code is None and not result.timed_out:  # 进程启动失败不是可解释的代码断言结果。
            raise RuntimeEnvironmentFailure("test_process_start_failed")  # 将本次运行归入基础设施错误。
        return result  # 返回完整 Runtime 结果供阶段判断。

    async def _initialize(self) -> None:  # 验证环境并建立基础提交索引。
        self._phase(AgentPhase.INITIALIZE)  # 进入正式状态机起点。
        await self._runtime.start(self._task)  # 由 Runtime 检查仓库、基础提交与隔离策略。
        self._index = await build_repo_index(self._runtime, self._task)  # 只在干净基础提交创建第四周索引。
        self._context.state.status = RunStatus.RUNNING  # 标记可执行环境已经准备好。
        self._context.manifest.status = RunStatus.RUNNING  # 同步运行清单状态。
        ManifestStore(self._context.layout.manifest_path).save(self._context.manifest)  # 持久化进入运行态的事实。
        self._issue_node = self._graph.add_node(EvidenceKind.ISSUE_FACT, self._task.problem_statement[:2_000], EvidenceOrigin.ISSUE, f"task:{self._task.task_id}")  # 保存原始 Issue 事实锚点。
        self._sync_graph()  # 保存初始图快照。

    async def _understand(self) -> None:  # 把 Issue 分解为预期、现状、未知和复现计划。
        self._phase(AgentPhase.UNDERSTAND)  # 执行合法阶段迁移。
        decision, event_id = await self._ask(AgentPhase.UNDERSTAND, "分解 Issue：区分原文约束、期望、尚未观测的现状、未知项和公开复现计划；actual 未执行时必须说明未知。", IssueUnderstanding)  # 请求严格结构化理解。
        summary = f"预期：{decision.expected}；现状：{decision.actual}；未知：{'、'.join(decision.unknowns)}"[:2_000]  # 保留紧凑且不伪装为事实的理解。
        node = self._graph.add_node(EvidenceKind.DECISION, summary, EvidenceOrigin.MODEL, f"event:{event_id}", metadata={"stage": "understand"})  # 以模型推断来源保存理解结果。
        self._graph.add_edge(node.node_id, self._issue_node.node_id, EvidenceRelation.DERIVED_FROM, f"event:{event_id}")  # 连接到原始 Issue 原文。
        self._sync_graph()  # 保存分解后的证据图。

    async def _reproduce(self) -> None:  # 在候选修改前运行公开测试获取事实信号。
        self._phase(AgentPhase.REPRODUCE)  # 进入公开复现阶段。
        if not self._task.public_commands:  # 部分任务没有可用的公开复现命令。
            self._record(EventType.DECISION_RECORDED, EventActor.AGENT, {"phase": "reproduce", "result": "no_public_commands"})  # 明确记录没有复现而非伪造通过。
            return  # 后续静态定位仍可继续，但最终不能报告已验证成功。
        for command in self._task.public_commands:  # 对任务明确给出的每条公开测试执行一次。
            result = await self._execute_test(command)  # 记录 Runtime 事件与实际失败节点。
            self._context.state.executed_tests.add(command)  # 保存已尝试的公开测试。
            if not result.succeeded:  # 首个失败已提供最直接的修复前信号。
                break  # 避免不必要的额外命令成本。

    def _localize(self) -> None:  # 融合 Issue 与当前真实失败输出。
        self._phase(AgentPhase.LOCALIZE)  # 进入可回跳的定位阶段。
        result = localize(self._index, self._task.problem_statement, traceback=self._last_test_output, failing_tests=self._failed_tests)  # 计算文件与符号 Top-K。
        if not result.files or not result.symbols:  # 无法形成可执行的根因假设目标。
            raise StrategyStop("localization_empty")  # 避免让模型凭空编造仓库位置。
        signature = tuple((candidate.path, candidate.symbol) for candidate in (*result.files, *result.symbols))  # 固定本轮排序指纹。
        if signature == self._last_localization_signature:  # 反思后未得到任何新的定位排序。
            self._record(EventType.DECISION_RECORDED, EventActor.AGENT, {"phase": "localize", "reason": "repeated_localization"})  # 审计重复定位。
        self._last_localization_signature = signature  # 保存最近定位指纹供下轮比较。
        self._ranked_files = {candidate.path for candidate in result.files}  # 限制计划目标到已召回文件。
        self._ranked_symbols = {(candidate.path, candidate.symbol) for candidate in result.symbols if candidate.path in self._ranked_files}  # 限制计划目标同时落在文件与符号 Top-K。
        if not self._ranked_symbols:  # 文件与符号 Top-K 没有可执行的共同目标。
            raise StrategyStop("localization_no_joint_candidate")  # 不让模型凭空组合不一致的路径与符号。
        self._file_nodes: dict[str, str] = {}  # 保存本轮文件路径到图节点的映射。
        for candidate in result.files:  # 将每个高排名文件写入证据图。
            detail = f"{candidate.path}；定位分数 {candidate.score}；证据 {json.dumps(candidate.evidence, ensure_ascii=False)[:1_200]}"  # 保留证据而不是只有模型结论。
            node = self._graph.add_node(EvidenceKind.FILE, detail[:2_000], EvidenceOrigin.DERIVED, f"index:{self._index.cache_key}", metadata={"path": candidate.path, "score": candidate.score})  # 保存可追溯的确定性文件候选。
            self._file_nodes[candidate.path] = node.node_id  # 供符号和补丁关系复用。
            self._context.state.visited_files.add(candidate.path)  # 更新当前定位过的文件集合。
        indexed = {item.path: item for item in self._index.files}  # 构造候选源文件查询表。
        modules = {path: path.removesuffix(".py").replace("/", ".") for path in self._file_nodes}  # 将高排名文件映射到静态模块名称。
        for source_path, source_id in self._file_nodes.items():  # 枚举文件之间可确定的导入关系。
            for target_path, module_name in modules.items():  # 只连接当前 Top-K 内的目标文件。
                if source_path != target_path and module_name in indexed[source_path].imports:  # AST 确认源文件导入目标模块。
                    self._graph.add_edge(source_id, self._file_nodes[target_path], EvidenceRelation.IMPORTS, f"index:{self._index.cache_key}")  # 保存可追溯的静态导入边。
                    if source_path.rsplit("/", 1)[-1].startswith("test_"):  # 测试文件导入源码时形成弱覆盖关系。
                        self._graph.add_edge(source_id, self._file_nodes[target_path], EvidenceRelation.COVERS, f"index:{self._index.cache_key}")  # 明确它只是静态关系而非测试覆盖率证明。
        symbol_nodes: dict[tuple[str, str | None], str] = {}  # 保存本轮符号位置到节点 ID 的映射。
        for candidate in result.symbols:  # 为高排名符号附上有限代码上下文。
            source = indexed[candidate.path]  # 读取基础提交中已经受限的源码行。
            first = max(0, (candidate.line or 1) - 1)  # 转换为零开始计数切片。
            excerpt = "\n".join(source.lines[first : first + 5])[:700]  # 最多展示五行代码，避免挤掉失败证据。
            detail = f"{candidate.path}:{candidate.line} {candidate.symbol}；定位分数 {candidate.score}\n{excerpt}"  # 汇总符号位置和局部源码。
            node = self._graph.add_node(EvidenceKind.SYMBOL, detail[:2_000], EvidenceOrigin.DERIVED, f"index:{self._index.cache_key}", metadata={"path": candidate.path, "symbol": candidate.symbol, "line": candidate.line})  # 保存确定性符号候选。
            symbol_nodes[(candidate.path, candidate.symbol)] = node.node_id  # 供 AST 调用关系引用。
            if candidate.path in self._file_nodes:  # 当前 Top-K 文件中可找到符号所属文件。
                self._graph.add_edge(self._file_nodes[candidate.path], node.node_id, EvidenceRelation.DEFINES, f"index:{self._index.cache_key}")  # 明确文件定义关系。
            self._context.state.visited_symbols.add(f"{candidate.path}:{candidate.symbol}")  # 记录已定位符号。
        for source_path, source_id in self._file_nodes.items():  # 根据静态调用名称连接高排名目标符号。
            for (_target_path, symbol_name), target_id in symbol_nodes.items():  # 检查本轮符号定义。
                if symbol_name is not None and symbol_name.rsplit(".", 1)[-1] in indexed[source_path].calls:  # 只把 AST 调用末级名称作为弱关联。
                    self._graph.add_edge(source_id, target_id, EvidenceRelation.CALLS, f"index:{self._index.cache_key}")  # 保留来源并避免宣称动态调用图完整。
        self._sync_graph()  # 持久化定位候选及其关系。

    async def _plan(self) -> None:  # 为高排名位置生成有限、可证伪的根因方案。
        self._phase(AgentPhase.PLAN)  # 进入计划阶段。
        decision, event_id = await self._ask(AgentPhase.PLAN, "根据定位证据提出一个可检验根因和单文件最小修复方案；target_file/target_symbol 必须来自候选，说明风险、公开验证及放弃条件。", RepairPlan)  # 获取严格计划结构。
        if (decision.target_file, decision.target_symbol) not in self._ranked_symbols:  # 阻止模型选择未读或不存在的代码位置。
            raise ModelOutputError("计划目标不在本轮已定位符号中")  # 不让幻觉路径进入补丁阶段。
        if any(node.kind is EvidenceKind.HYPOTHESIS and node.status is HypothesisStatus.REFUTED and node.text.casefold() == decision.hypothesis.casefold() for node in self._graph.nodes.values()):  # 检查是否重提已否定的同一根因。
            raise StrategyStop("repeated_refuted_hypothesis")  # 不让失败方案被上下文压缩后偷偷恢复。
        hypothesis = self._graph.add_node(EvidenceKind.HYPOTHESIS, decision.hypothesis, EvidenceOrigin.MODEL, f"event:{event_id}", metadata={"target_file": decision.target_file, "target_symbol": decision.target_symbol})  # 明确将根因保留为推断。
        plan_text = f"修改 {decision.target_file}:{decision.target_symbol}；预期：{decision.expected_change}；风险：{decision.regression_risk}；验证：{decision.verification}；放弃条件：{decision.abandon_if}"  # 保留可证伪计划要素。
        plan = self._graph.add_node(EvidenceKind.PLAN_STEP, plan_text[:2_000], EvidenceOrigin.MODEL, f"event:{event_id}")  # 保存模型提出的单步方案。
        self._graph.add_edge(plan.node_id, hypothesis.node_id, EvidenceRelation.DERIVED_FROM, f"event:{event_id}")  # 将修复方案绑定根因。
        self._graph.add_edge(self._file_nodes[decision.target_file], hypothesis.node_id, EvidenceRelation.SUPPORTS, f"event:{event_id}")  # 只把静态定位当作弱支持关系。
        self._context.state.main_hypothesis = hypothesis.node_id  # 保存当前待验证假设的图 ID。
        self._planned_file = decision.target_file  # 保存本轮计划允许修改的目标文件。
        self._context.state.plan = [plan_text]  # 同步状态中的可阅读计划。
        self._sync_graph()  # 持久化计划及假设。

    async def _generate(self, *, already_entered: bool = False) -> tuple[str | None, str]:  # 生成并应用一份候选补丁。
        if not already_entered:  # 普通计划路径尚未进入补丁生成阶段。
            self._phase(AgentPhase.GENERATE_CANDIDATES)  # 通过正式迁移表进入单候选生成阶段。
        decision, event_id = await self._ask(AgentPhase.GENERATE_CANDIDATES, "只生成与当前计划一致的标准 Git unified diff；不要描述或执行测试，返回 patch 字段。", PatchProposal)  # 请求严格补丁 JSON。
        digest = hashlib.sha256(decision.patch.encode("utf-8")).hexdigest()  # 计算不含模型上下文的候选标识。
        if self._graph.has_patch_digest(digest):  # 同一补丁已经失败或被拒绝过。
            raise StrategyStop("duplicate_patch")  # 防止无效候选无限循环。
        candidate_id = digest[:16]  # 使用稳定短 ID 对齐候选、测试与轨迹。
        self._current_candidate_id = candidate_id  # 保存本次失败反思需要匹配的候选身份。
        artifact = self._context.layout.run_dir / "candidates" / f"{candidate_id}.patch"  # 在当前运行目录保存补丁原文。
        artifact.write_text(decision.patch, encoding="utf-8")  # 保留可审计的完整候选而不把大补丁塞进事件。
        attempt = self._graph.add_node(EvidenceKind.PATCH_ATTEMPT, f"候选 {candidate_id}；目标 {self._context.state.plan[-1][:250]}", EvidenceOrigin.MODEL, f"event:{event_id}", metadata={"digest": digest, "candidate_id": candidate_id})  # 保存模型提出的候选身份。
        self._context.state.candidate_ids.append(candidate_id)  # 维护本次运行候选顺序。
        self._current_attempt = attempt.node_id  # 供后续验证结果建立因果关系。
        if self._context.state.usage.tool_calls >= self._task.budget.max_tool_calls:  # 应用补丁也算受控工具动作。
            raise StrategyStop("tool_calls")  # 防止越过动作上限。
        self._budget()  # 模型生成补丁后再次检查时间、token 与成本上限。
        self._context.state.usage.tool_calls += 1  # 记录一次补丁操作尝试。
        self._record(EventType.TOOL_REQUESTED, EventActor.AGENT, {"name": "apply_patch", "candidate_id": candidate_id, "digest": digest})  # 轨迹中记录候选摘要。
        applied = await self._runtime.apply_patch(decision.patch)  # 由 Runtime 验证路径并原子应用补丁。
        event = self._record(EventType.PATCH_APPLIED, EventActor.RUNTIME, {"candidate_id": candidate_id, "applied": applied.applied, "changed_files": list(applied.changed_files), "reason": applied.rejection_reason})  # 保留可应用性事实。
        if not applied.applied:  # 无效 diff 也应写入失败图供反思。
            detail = (applied.rejection_reason or applied.stderr or "补丁无法应用")[:1_800]  # 保存可读拒绝原因。
            failure = self._graph.add_node(EvidenceKind.FAILURE, detail, EvidenceOrigin.RUNTIME, f"event:{event}", metadata={"candidate_id": candidate_id})  # 明确补丁拒绝为 Runtime 事实。
            self._graph.add_edge(attempt.node_id, failure.node_id, EvidenceRelation.CAUSES, f"event:{event}")  # 将失败归属到特定候选。
            self._context.state.last_failure = detail  # 更新最近失败状态。
            self._sync_graph()  # 保存被拒候选与失败原因。
            return None, candidate_id  # 不进入测试阶段，直接反思。
        if set(applied.changed_files) != {self._planned_file}:  # 第五周单文件计划不允许候选暗中修改其他路径。
            detail = f"候选修改文件 {list(applied.changed_files)} 与计划目标 {self._planned_file} 不一致"  # 形成明确的计划一致性失败。
            failure = self._graph.add_node(EvidenceKind.FAILURE, detail, EvidenceOrigin.RUNTIME, f"event:{event}", metadata={"candidate_id": candidate_id})  # 保存实际修改文件不匹配的事实。
            self._graph.add_edge(attempt.node_id, failure.node_id, EvidenceRelation.CAUSES, f"event:{event}")  # 将失败与具体补丁关联。
            self._context.state.last_failure = detail  # 让后续反思看到不一致原因。
            self._sync_graph()  # 落盘候选与拒绝依据。
            return None, candidate_id  # 跳过测试并在反思后重置工作区。
        self._modified_files.update(applied.changed_files)  # 保存当前候选真实修改文件。
        for path in applied.changed_files:  # 将实际 diff 路径绑定到定位文件。
            if path in self._file_nodes:  # 文件已经有定位节点。
                self._graph.add_edge(attempt.node_id, self._file_nodes[path], EvidenceRelation.MODIFIES, f"event:{event}")  # 记录候选与目标文件关系。
        self._expected_diff = await self._runtime.get_diff()  # 保存测试前真实差异以检测验证阶段污染。
        self._sync_graph()  # 保存已应用补丁状态。
        return decision.patch, candidate_id  # 返回补丁供验证或最终导出。

    async def _verify(self, candidate_id: str) -> bool:  # 对当前单候选执行全部公开测试。
        self._phase(AgentPhase.VERIFY_CANDIDATES)  # 进入候选验证阶段。
        if not self._task.public_commands:  # 没有公开测试就不能声称内部验证通过。
            raise StrategyStop("missing_public_commands")  # 保守停止并保留候选 artifact。
        passed = True  # 后续任一公开命令失败都会覆盖结果。
        for command in self._task.public_commands:  # 逐个运行任务定义的公开检查。
            result = await self._execute_test(command, candidate_id=candidate_id)  # 获取真实 Runtime 观察。
            verification_event = self._record(EventType.VERIFICATION_COMPLETED, EventActor.VERIFIER, {"candidate_id": candidate_id, "command": command, "passed": result.succeeded, "return_code": result.return_code})  # 单独保存候选验证结论。
            node = self._graph.add_node(EvidenceKind.VERIFICATION, f"{command}：{'通过' if result.succeeded else '失败'}；退出码 {result.return_code}", EvidenceOrigin.VERIFICATION, f"event:{verification_event}", metadata={"candidate_id": candidate_id, "passed": result.succeeded})  # 保存真实验证节点。
            self._graph.add_edge(self._current_attempt, node.node_id, EvidenceRelation.VERIFIES, f"event:{verification_event}")  # 保留验证与正确候选的对应关系。
            self._sync_graph()  # 将候选验证状态立即落盘。
            if not result.succeeded:  # 任一失败则本候选不合格。
                passed = False  # 更新本轮整体结论。
                break  # 不浪费额外验证预算。
        return passed  # 仅在全部公开命令通过时返回真。

    async def _reflect(self) -> ReflectionAction:  # 基于真实失败进行结构化阶段路由。
        self._phase(AgentPhase.REFLECT)  # 进入可回跳的反思阶段。
        decision, event_id = await self._ask(AgentPhase.REFLECT, "依据最近补丁拒绝或测试失败，指出被否定预期、新事实、失败类别和下一阶段；不能声称失败测试已确认假设；不要重复被反驳方案。", ReflectionDecision)  # 请求严格失败分析。
        latest_verification = self._graph.latest(EvidenceKind.VERIFICATION)  # 查询本轮实际验证结果。
        hypothesis_id = self._context.state.main_hypothesis  # 读取当前根因假设节点 ID。
        if decision.hypothesis_status is HypothesisStatus.CONFIRMED:  # 失败反思中不允许模型自称根因已获证实。
            raise ModelOutputError("失败后不能把假设标记为已确认")  # 防止模型推断洗白为验证事实。
        if hypothesis_id is not None:  # 只更新确实存在的当前假设。
            status = decision.hypothesis_status  # 读取经过枚举校验的模型建议状态。
            if status is HypothesisStatus.REFUTED and (latest_verification is None or latest_verification.metadata.get("candidate_id") != self._current_candidate_id or latest_verification.metadata.get("passed") is not False):  # 只有本候选的失败验证才可反驳根因。
                status = HypothesisStatus.ABANDONED  # 没有实际测试反证时只放弃本方案。
            self._graph.update_hypothesis(hypothesis_id, status, evidence_id=latest_verification.node_id if latest_verification else None)  # 由图校验强状态必须有验证证据。
            if status in {HypothesisStatus.REFUTED, HypothesisStatus.ABANDONED}:  # 已否定根因不能继续是主假设。
                self._context.state.main_hypothesis = None  # 从当前状态移除主假设引用。
        detail = f"被否定预期：{decision.disconfirmed_expectation}；新事实候选：{decision.new_fact}；类别：{decision.failure_kind.value}；下一步：{decision.next_action.value}；理由：{decision.reason}"  # 将反思保留为模型推断文本。
        node = self._graph.add_node(EvidenceKind.DECISION, detail[:2_000], EvidenceOrigin.MODEL, f"event:{event_id}", metadata={"next_action": decision.next_action.value, "repeated_attempt": decision.repeated_attempt})  # 不把模型转述的新事实当执行事实。
        failure = self._graph.latest(EvidenceKind.FAILURE)  # 寻找反思所依据的实际失败节点。
        if failure is not None:  # 反思有执行失败可以溯源。
            self._graph.add_edge(node.node_id, failure.node_id, EvidenceRelation.DERIVED_FROM, f"event:{event_id}")  # 将模型决定连接到实际失败。
        self._record(EventType.DECISION_RECORDED, EventActor.AGENT, {"phase": "reflect", "action": decision.next_action.value, "hypothesis_status": decision.hypothesis_status.value, "failure_kind": decision.failure_kind.value, "repeated_attempt": decision.repeated_attempt})  # 保存结构化反思路由。
        self._sync_graph()  # 持久化假设状态和决定。
        action = decision.next_action  # 保留模型提出的下一步。
        if self._context.state.main_hypothesis is None and action is ReflectionAction.REPAIR:  # 被反驳的根因不能直接继续生成同方案补丁。
            action = ReflectionAction.REPLAN  # 强制回到计划阶段换方案。
        return action  # 返回经系统约束后的合法路由。

    async def _drive(self) -> AgentOutcome:  # 按显式状态机执行一次完整任务。
        await self._initialize()  # 建立 Runtime、索引、Issue 事实与事件轨迹。
        await self._understand()  # 结构化理解 Issue 约束和未知项。
        await self._reproduce()  # 尽可能取得修复前公开测试信号。
        self._localize()  # 融合 Issue、静态代码和失败反馈生成候选位置。
        await self._plan()  # 创建第一个可证伪根因假设。
        reflections = 0  # 记录已经执行的结构化反思轮次。
        generation_ready = False  # 标记是否已由 REFLECT 合法进入补丁生成阶段。
        while True:  # 仅在有限预算和反思上限内重试单候选。
            patch, candidate_id = await self._generate(already_entered=generation_ready)  # 应用一个新补丁或记录补丁拒绝。
            generation_ready = False  # 下一轮默认需要正常阶段迁移。
            verified = await self._verify(candidate_id) if patch is not None else False  # 只对应用成功的候选运行测试。
            if verified:  # 全部公开命令通过时选择当前候选。
                self._phase(AgentPhase.SELECT_AND_FINALIZE)  # 进入合法的最终选择阶段。
                exported = await self._runtime.get_diff()  # 直接从 Runtime 导出真实工作区差异。
                if not exported:  # 空 diff 不能算已修复。
                    raise StrategyStop("empty_patch")  # 拒绝模型空修改自称成功。
                if exported != self._expected_diff:  # 公开测试可能额外修改源码或产生未忽略的新文件。
                    raise StrategyStop("workspace_modified_during_verification")  # 不把测试副作用混进最终补丁。
                hypothesis_id = self._context.state.main_hypothesis  # 读取被本次验证支持的假设。
                verification = self._graph.latest(EvidenceKind.VERIFICATION)  # 获取最新实际通过结果。
                if hypothesis_id is not None and verification is not None:  # 确认有可追溯的验证节点。
                    self._graph.update_hypothesis(hypothesis_id, HypothesisStatus.CONFIRMED, evidence_id=verification.node_id)  # 用实际测试支持根因假设。
                self._context.state.selected_candidate_id = candidate_id  # 保存被选中候选 ID。
                self._sync_graph()  # 写出最终图状态。
                return AgentOutcome(RunStatus.SUCCEEDED, "public_tests_passed", exported)  # 仅声称内部公开测试通过。
            if reflections >= self._config.max_reflection_rounds:  # 用完反思次数后停止无效尝试。
                return AgentOutcome(RunStatus.FAILED, "reflection_rounds", None)  # 不导出未验证补丁。
            reflections += 1  # 在模型反思前占用一轮预算。
            action = await self._reflect()  # 更新假设状态并选择下一阶段。
            if action is ReflectionAction.STOP:  # 模型给出基于证据的停止决定。
                return AgentOutcome(RunStatus.FAILED, "reflection_stop", None)  # 保存失败而非虚构成功。
            await self._runtime.reset()  # 第五周在同一候选容器内恢复基础提交，第六周再做独立分支。
            self._modified_files.clear()  # 清除已经被回滚的当前工作区修改。
            if action is ReflectionAction.RELOCALIZE:  # 新错误信息可能改变目标文件或符号。
                self._localize()  # 依据新失败重新排名。
                await self._plan()  # 为新的位置创建可证伪方案。
            elif action is ReflectionAction.REPLAN:  # 保留定位但更换根因或修改层级。
                await self._plan()  # 新假设必须通过重复反证检查。
            else:  # 修复错误但根因仍有支持时允许重写补丁。
                self._phase(AgentPhase.GENERATE_CANDIDATES)  # 状态机允许 REFLECT 直接回到生成阶段。
                generation_ready = True  # 告知下一轮生成器不重复执行同一阶段迁移。

    async def run(self, task: TaskSpec, context: RunContext, runtime: Runtime) -> AgentOutcome:  # 对外执行一次独立主策略运行。
        if context.state.task_id != task.task_id or context.manifest.task_id != task.task_id or context.manifest.run_id != context.state.run_id:  # 验证任务与运行目录归属。
            raise ValueError("运行上下文与任务不匹配")  # 防止事件和补丁交叉写入。
        if context.state.phase is not AgentPhase.CREATED or context.manifest.status is not RunStatus.PENDING:  # 拒绝重复消费已有运行。
            raise ValueError("运行上下文必须处于初始状态")  # 防止覆盖前次任务结果。
        self._task = task  # 保存不可变任务定义。
        self._context = context  # 保存状态、清单和事件存储。
        self._runtime = runtime  # 保存唯一允许执行代码的 Runtime。
        self._graph = EvidenceGraph()  # 为本次运行创建独立证据图。
        self._index: RepoIndex  # 在 INITIALIZE 完成后填入基础提交索引。
        self._modified_files: set[str] = set()  # 跟踪当前未回滚候选修改。
        self._failed_tests: tuple[str, ...] = ()  # 跟踪最新失败测试名称。
        self._last_test_output = ""  # 跟踪最新真实测试输出供定位。
        self._last_localization_signature: tuple[tuple[str, str | None], ...] = ()  # 跟踪重复定位结果。
        self._last_event_id = context.event_store.load_all()[-1].event_id  # 从 RUN_CREATED 建立因果链。
        self._started_at = time.perf_counter()  # 记录包含 Runtime 启动的总时间。
        context.manifest.started_at = datetime.now(UTC)  # 保存运行实际开始时间。
        context.manifest.status = RunStatus.PREPARING  # 在环境准备前保持保守状态。
        ManifestStore(context.layout.manifest_path).save(context.manifest)  # 持久化运行入口。
        outcome = AgentOutcome(RunStatus.INFRASTRUCTURE_ERROR, "not_started", None)  # 初始化默认失败结果。
        try:  # 为整个自主任务施加硬墙钟上限。
            outcome = await asyncio.wait_for(self._drive(), timeout=task.budget.max_wall_clock_seconds)  # 执行显式阶段状态机。
        except TimeoutError:  # 整体时间预算耗尽。
            outcome = AgentOutcome(RunStatus.FAILED, "wall_clock_seconds", None)  # 不输出未验证候选。
        except StrategyStop as error:  # 策略检测到预算或重复方案。
            outcome = AgentOutcome(RunStatus.FAILED, error.reason, None)  # 返回机器可读终止原因。
        except RuntimeEnvironmentFailure as error:  # 公开测试进程无法启动时不能归因于补丁质量。
            outcome = AgentOutcome(RunStatus.INFRASTRUCTURE_ERROR, error.reason, None)  # 保存独立基础设施错误分类。
        except ModelProviderError as error:  # 外部模型服务故障与修复能力分开统计。
            outcome = AgentOutcome(RunStatus.INFRASTRUCTURE_ERROR, f"model_{error.kind}", None)  # 隐藏可能包含密钥的服务错误正文。
        except (ModelOutputError, ValidationError):  # 严格阶段 JSON 不可解析或违反根因约束。
            outcome = AgentOutcome(RunStatus.FAILED, "invalid_model_output", None)  # 不执行无法信任的补丁。
        except ContextTooLargeError:  # 必保留证据无法放入模型上下文。
            outcome = AgentOutcome(RunStatus.FAILED, "context_limit", None)  # 不静默丢弃关键反证。
        except Exception as error:  # 未预期异常必须归为基础设施错误。
            outcome = AgentOutcome(RunStatus.INFRASTRUCTURE_ERROR, type(error).__name__, None)  # 不把环境 bug 计为 Agent 失败。
        finally:  # 无论哪条退出路径都关闭 Runtime 并更新工件。
            try:  # 容器关闭错误需要单独覆盖成功结果。
                await runtime.close()  # 释放进程、容器与临时文件。
            except Exception as error:  # 环境未成功清理时结果不可报告为成功。
                outcome = AgentOutcome(RunStatus.INFRASTRUCTURE_ERROR, f"runtime_close_{type(error).__name__}", None)  # 保守覆盖终态。
            context.state.usage.wall_clock_seconds = time.perf_counter() - self._started_at  # 保存真实总墙钟耗时。
            context.state.status = outcome.status  # 同步当前运行状态。
            context.state.stop_reason = outcome.stop_reason  # 保存终止理由。
            target = AgentPhase.COMPLETED if outcome.status is RunStatus.SUCCEEDED else AgentPhase.FAILED  # 选择合法终态。
            if not context.state.is_terminal:  # 只有尚未进入终态时执行迁移。
                context.state.transition_to(target)  # 使用正式状态迁移表而非直接赋值。
                self._record(EventType.PHASE_CHANGED, EventActor.AGENT, {"phase": target.value})  # 轨迹中记录最终阶段。
            if outcome.patch is not None:  # 只有实际通过公开测试的补丁能进入最终产物。
                context.layout.final_patch_path.write_text(outcome.patch, encoding="utf-8")  # 保存 Runtime 导出的标准 diff。
                context.manifest.final_patch_path = str(context.layout.final_patch_path)  # 关联清单中的最终补丁路径。
            context.manifest.status = outcome.status  # 持久化真实终态。
            context.manifest.stop_reason = outcome.stop_reason  # 持久化机器可读原因。
            context.manifest.finished_at = datetime.now(UTC)  # 记录结束时间。
            ManifestStore(context.layout.manifest_path).save(context.manifest)  # 原子保存最终清单。
            self._sync_graph()  # 即使异常中断也保存最后可用的图快照。
            event_type = EventType.RUN_COMPLETED if outcome.status is RunStatus.SUCCEEDED else EventType.RUN_FAILED  # 区分任务完成与失败。
            self._record(event_type, EventActor.AGENT, {"status": outcome.status.value, "reason": outcome.stop_reason})  # 写入终止事件。
        return outcome  # 返回与最终清单和图状态一致的结果。
