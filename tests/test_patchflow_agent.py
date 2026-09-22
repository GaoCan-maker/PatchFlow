"""第五周主策略成功、反思回跳、重复补丁和无效模型输出测试。"""  # 全部使用 FakeModel 和可信临时 Git 仓库。

from __future__ import annotations  # 延迟解析测试辅助类型。

import asyncio  # 在同步 pytest 中运行完整异步状态机。
import json  # 构造严格阶段 JSON 并检查图快照。
import sys  # 为可信本地测试使用当前解释器绝对路径。
from pathlib import Path  # 使用 pytest 临时运行目录。

from patchflow.agent.patchflow import PatchFlowAgent  # 导入第五周独立主策略。
from patchflow.application.run_initializer import initialize_run  # 创建正式运行 artifact 与事件流。
from patchflow.config.models import (  # 配置主策略和可信本地测试后端。
    AgentConfig,  # 设置最大反思轮次。
    AppConfig,  # 构造正式运行快照。
    RuntimeConfig,  # 声明可信本地测试后端。
)  # 完成配置类型导入。
from patchflow.domain.enums import AgentPhase, EventType, RunStatus  # 断言显式阶段和终态。
from patchflow.domain.runtime import CommandResult  # 标注测试 Runtime 包装器的真实返回类型。
from patchflow.domain.task import Budget  # 构造主策略资源上限测试任务。
from patchflow.model.fake import FakeModel  # 逐步提供确定性离线决策。
from patchflow.model.protocol import ModelResponse  # 构造正式模型回复对象。
from patchflow.runtime.local import LocalRuntime  # 只在临时隔离仓库中执行公开测试。
from tests.runtime_helpers import (  # 复用已审计仓库与补丁夹具。
    create_temporary_git_repository,  # 生成可信临时 Git 仓库。
    valid_patch,  # 生成可应用且预期通过测试的补丁。
)  # 完成测试夹具导入。


def _reply(payload: dict[str, object]) -> ModelResponse:  # 创建符合模型协议的严格 JSON 文本回复。
    return ModelResponse(final_answer=json.dumps(payload, ensure_ascii=False))  # FakeModel 不调用网络或 API key。


def _understanding() -> ModelResponse:  # 构造 Issue 理解阶段脚本回复。
    return _reply({"summary": "修复 greet 输入空白造成的问候行为", "expected": "姓名两端空白应被移除", "actual": "修复前尚未观测带空白输入的公开测试", "constraints": ["保留正常姓名行为"], "unknowns": ["隐藏测试是否包含更多空白类型"], "reproduction_plan": "运行任务给出的公开 pytest 命令"})  # 保留实际与未知的区别。


def _plan(hypothesis: str) -> ModelResponse:  # 构造可证伪的单文件修复方案。
    return _reply({"hypothesis": hypothesis, "target_file": "app.py", "target_symbol": "greet", "expected_change": "先去掉输入姓名两端空白再插入问候字符串", "regression_risk": "正常姓名输出可能变化", "verification": "运行任务给出的 pytest 回归测试", "abandon_if": "公开测试仍失败或补丁不可应用"})  # 让根因和验证条件显式化。


def _reflection(*, status: str, action: str) -> ModelResponse:  # 构造失败后的严格结构化反思。
    return _reply({"disconfirmed_expectation": "上一候选应保持问候前缀", "new_fact": "公开测试显示上一候选破坏正常姓名输出", "hypothesis_status": status, "failure_kind": "patch_error", "next_action": action, "reason": "公开测试断言失败，需改变候选修复方案", "repeated_attempt": False})  # 提供可被图与状态机核验的路由。


def _run_script(tmp_path: Path, responses: tuple[ModelResponse, ...], *, reflections: int = 2, command: str | None = None, budget: Budget | None = None) -> tuple[object, object, FakeModel]:  # 在一个独立 Git 仓库中运行主策略。
    fixture = create_temporary_git_repository(tmp_path)  # 创建可信临时仓库和正式 TaskSpec。
    task = fixture.task.model_copy(update={"public_commands": (command or f"{sys.executable} -m pytest -q",), "budget": budget or fixture.task.budget})  # 非交互 WSL 的 PATH 不保证短命令 python 指向可执行解释器。
    config = AppConfig(agent=AgentConfig(strategy="patchflow", max_reflection_rounds=reflections), runtime=RuntimeConfig(kind="local"))  # 保留单候选第五周策略。
    context = initialize_run(task, config, runs_root=tmp_path / "runs")  # 创建清单、事件和补丁目录。
    model = FakeModel(responses)  # 保存可检查请求内容的离线模型。
    agent = PatchFlowAgent(model, config=config.agent)  # 装配第五周状态机。
    runtime = LocalRuntime(fixture.repository, fixture.isolation_root)  # 只允许访问临时隔离根。
    outcome = asyncio.run(agent.run(task, context, runtime))  # 执行完整主策略。
    return outcome, context, model  # 返回终态、轨迹和模型请求供断言。


def test_main_strategy_reaches_verified_patch_with_explicit_phases(tmp_path: Path) -> None:  # 验证一轮成功修复闭环。
    responses = (_understanding(), _plan("greet 未规范化输入姓名导致额外空白"), _reply({"patch": valid_patch()}))  # 依次准备理解、计划和补丁决策。
    outcome, context, model = _run_script(tmp_path, responses)  # 在隔离仓库执行主策略。
    assert outcome.status is RunStatus.SUCCEEDED and outcome.patch is not None  # 只有公开测试通过才产生最终补丁。
    assert context.state.phase is AgentPhase.COMPLETED  # 状态机应进入正式成功终态。
    assert context.layout.final_patch_path.read_text(encoding="utf-8") == outcome.patch  # 最终 artifact 必须来自 Runtime diff。
    graph = json.loads((context.layout.run_dir / "evidence_graph.json").read_text(encoding="utf-8"))  # 读取最终工作记忆。
    assert any(node["kind"] == "hypothesis" and node["status"] == "confirmed" and node["origin"] == "model" for node in graph["nodes"])  # 假设被实际验证支持但来源仍是模型。
    assert any(node["kind"] == "verification_result" and node["metadata"]["passed"] is True for node in graph["nodes"])  # 保存真实通过结果。
    assert any(edge["relation"] == "imports" for edge in graph["edges"])  # 测试文件与产品源码之间的 AST 导入边应可审计。
    assert any(edge["relation"] == "calls" for edge in graph["edges"])  # 现有公开测试调用 greet 应形成静态调用边。
    assert any(edge["relation"] == "covers" for edge in graph["edges"])  # 测试导入关系应保留弱覆盖证据。
    phases = [event.payload["phase"] for event in context.event_store.load_all() if event.event_type is EventType.PHASE_CHANGED]  # 从事件流提取阶段序列。
    assert phases == ["initialize", "understand", "reproduce", "localize", "plan", "generate_candidates", "verify_candidates", "select_and_finalize", "completed"]  # 检查状态机未跳阶段。
    assert len(model.requests) == 3  # 成功路径仅发生三个离线模型决策。


def test_failed_candidate_refutes_hypothesis_and_replans(tmp_path: Path) -> None:  # 验证反证进入上下文并改变下一步。
    wrong = valid_patch().replace('Hello, {cleaned_name}', 'Oops, {cleaned_name}')  # 制造可应用但公开测试失败的候选。
    responses = (_understanding(), _plan("前缀错误是唯一根因"), _reply({"patch": wrong}), _reflection(status="refuted", action="replan"), _plan("输入姓名未经规范化导致额外空白"), _reply({"patch": valid_patch()}))  # 先失败再提出不同根因。
    outcome, context, model = _run_script(tmp_path, responses)  # 执行两轮单候选状态机。
    assert outcome.status is RunStatus.SUCCEEDED  # 第二个候选通过公开回归测试。
    graph = json.loads((context.layout.run_dir / "evidence_graph.json").read_text(encoding="utf-8"))  # 检查最终证据图。
    hypotheses = [node for node in graph["nodes"] if node["kind"] == "hypothesis"]  # 收集两次模型根因。
    assert [node["status"] for node in hypotheses] == ["refuted", "confirmed"]  # 旧假设不能偷偷恢复，新的可由验证确认。
    assert any(edge["relation"] == "refutes" and edge["target_id"] == hypotheses[0]["node_id"] for edge in graph["edges"])  # 第一轮公开失败直接反驳旧假设。
    second_plan_context = json.loads(model.requests[4].history[-1].content)["state"]  # 读取第二次计划的真实模型上下文。
    assert second_plan_context["blocked_hypotheses"][0]["id"] == hypotheses[0]["node_id"]  # 新一轮模型看到了被否定方案。
    assert second_plan_context["current_failure"]["kind"] == "failure"  # 最近失败证据没有被压缩丢失。
    phases = [event.payload["phase"] for event in context.event_store.load_all() if event.event_type is EventType.PHASE_CHANGED]  # 提取状态机回跳轨迹。
    assert phases.count("plan") == 2 and phases.count("verify_candidates") == 2 and "reflect" in phases  # 确认真实发生反思与重新计划。


def test_repair_branch_stops_duplicate_patch(tmp_path: Path) -> None:  # 验证修补分支不重复执行相同失败候选。
    wrong = valid_patch().replace('Hello, {cleaned_name}', 'Oops, {cleaned_name}')  # 准备第一次会失败的补丁。
    responses = (_understanding(), _plan("输入规范化不足导致问候失败"), _reply({"patch": wrong}), _reflection(status="partially_supported", action="repair"), _reply({"patch": wrong}))  # 模型第二次重复同一补丁。
    outcome, context, model = _run_script(tmp_path, responses)  # 运行受控反思与修补路径。
    assert outcome.status is RunStatus.FAILED and outcome.stop_reason == "duplicate_patch"  # 系统独立拒绝重复候选。
    assert context.state.phase is AgentPhase.FAILED  # 重复候选也应合法进入失败终态。
    assert len(model.requests) == 5  # 第二份补丁决策被读取但未再次应用。


def test_failed_verification_cannot_be_self_confirmed(tmp_path: Path) -> None:  # 验证模型不能把失败的测试解释为确认。
    wrong = valid_patch().replace('Hello, {cleaned_name}', 'Oops, {cleaned_name}')  # 构造可应用但回归失败的候选。
    responses = (_understanding(), _plan("前缀错误导致回归失败"), _reply({"patch": wrong}), _reflection(status="confirmed", action="repair"))  # 模型错误声称根因已确认。
    outcome, context, _ = _run_script(tmp_path, responses)  # 执行至失败反思边界。
    assert outcome.status is RunStatus.FAILED and outcome.stop_reason == "invalid_model_output"  # 阻止模型自我确认。
    assert not context.layout.final_patch_path.exists()  # 未经验证通过的补丁不能成为最终产物。


def test_reflection_can_relocalize_and_then_replan(tmp_path: Path) -> None:  # 验证失败反馈能走完整定位回跳。
    wrong = valid_patch().replace('Hello, {cleaned_name}', 'Oops, {cleaned_name}')  # 构造会失败但能应用的第一次候选。
    responses = (_understanding(), _plan("问候前缀逻辑是唯一根因"), _reply({"patch": wrong}), _reflection(status="refuted", action="relocalize"), _plan("greet 输入未先去除两端空白"), _reply({"patch": valid_patch()}))  # 让反思要求重新定位后换方案。
    outcome, context, _ = _run_script(tmp_path, responses)  # 执行失败、重定位与第二次验证。
    assert outcome.status is RunStatus.SUCCEEDED  # 新方案通过公开回归测试。
    phases = [event.payload["phase"] for event in context.event_store.load_all() if event.event_type is EventType.PHASE_CHANGED]  # 读取事件中的真实阶段序列。
    assert phases.count("localize") == 2 and phases.count("plan") == 2  # 验证不是仅在文本中声称重新定位。


def test_public_test_process_start_failure_is_infrastructure_error(tmp_path: Path) -> None:  # 验证环境故障不计为代码修复失败。
    outcome, context, model = _run_script(tmp_path, (_understanding(),), command="patchflow-public-command-that-does-not-exist")  # 在复现阶段执行不存在的公开命令。
    assert outcome.status is RunStatus.INFRASTRUCTURE_ERROR and outcome.stop_reason == "test_process_start_failed"  # 正确分类进程启动失败。
    assert context.state.phase is AgentPhase.FAILED  # 基础设施错误也应进入可审计终态。
    assert len(model.requests) == 1  # 环境不可用后不应继续支付模型定位费用。


def test_model_budget_stops_before_second_request(tmp_path: Path) -> None:  # 验证实际模型调用数硬限制。
    budget = Budget(max_model_calls=1)  # 只允许 UNDERSTAND 阶段的一次模型决策。
    outcome, context, model = _run_script(tmp_path, (_understanding(), _plan("greet 输入未规范化")), budget=budget)  # 第二条脚本回复不应被消费。
    assert outcome.status is RunStatus.FAILED and outcome.stop_reason == "model_calls"  # 正确报告达到调用次数预算。
    assert context.state.usage.model_calls == 1 and len(model.requests) == 1  # 不能偷偷发出第二次模型请求。


def test_verified_tests_cannot_smuggle_workspace_changes_into_patch(tmp_path: Path) -> None:  # 验证公开测试副作用不会污染最终补丁。
    fixture = create_temporary_git_repository(tmp_path)  # 创建仅供本测试修改的临时仓库。
    task = fixture.task.model_copy(update={"public_commands": (f"{sys.executable} -m pytest -q",)})  # 使用可执行的绝对解释器路径。
    config = AppConfig(agent=AgentConfig(strategy="patchflow"), runtime=RuntimeConfig(kind="local"))  # 配置可信本地测试策略。
    context = initialize_run(task, config, runs_root=tmp_path / "runs")  # 为本次污染检测创建独立轨迹。

    class SideEffectRuntime(LocalRuntime):  # 只在测试中模拟公开测试修改代码的副作用。
        def __init__(self) -> None:  # 初始化可信临时仓库 Runtime。
            super().__init__(fixture.repository, fixture.isolation_root)  # 继承所有真实路径和执行策略。
            self.test_calls = 0  # 区分修复前复现与候选后验证。

        async def execute(self, command: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:  # 拦截公开测试执行结果。
            result = await super().execute(command, timeout_seconds=timeout_seconds)  # 先运行真实 pytest。
            if "pytest" in command:  # 只统计公开测试命令。
                self.test_calls += 1  # 更新测试执行序号。
                if self.test_calls == 2 and result.succeeded:  # 候选验证通过后模拟隐藏副作用。
                    with (fixture.repository / "app.py").open("a", encoding="utf-8") as stream:  # 只修改临时仓库源码。
                        stream.write("# test side effect\n")  # 添加可由 Git diff 检测的额外变更。
            return result  # 保持真实命令退出码不变。

    model = FakeModel((_understanding(), _plan("greet 输入未规范化导致空白问题"), _reply({"patch": valid_patch()})))  # 提供一次本应成功的补丁路径。
    outcome = asyncio.run(PatchFlowAgent(model, config=config.agent).run(task, context, SideEffectRuntime()))  # 执行带测试副作用的真实状态机。
    assert outcome.status is RunStatus.FAILED and outcome.stop_reason == "workspace_modified_during_verification"  # 拒绝被测试污染的候选。
    assert not context.layout.final_patch_path.exists()  # 不得把额外源码变更导出为最终补丁。


def test_runtime_traceback_creates_provenance_linked_stack_frame(tmp_path: Path) -> None:  # 验证 traceback 不只参与排名也进入证据图。
    fixture = create_temporary_git_repository(tmp_path)  # 创建可信临时仓库。
    task = fixture.task.model_copy(update={"public_commands": (f"{sys.executable} -m pytest -q",)})  # 使用可识别的公开测试命令。
    config = AppConfig(agent=AgentConfig(strategy="patchflow"), runtime=RuntimeConfig(kind="local"))  # 配置离线主策略。
    context = initialize_run(task, config, runs_root=tmp_path / "runs")  # 为本次栈帧测试创建独立图快照。

    class TracebackRuntime(LocalRuntime):  # 在真实仓库索引之外仅替换公开测试输出。
        async def execute(self, command: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:  # 维持正式 Runtime 接口。
            if "pytest" in command:  # 只对公开测试返回明确失败栈帧。
                output = 'Traceback (most recent call last):\n  File "/work/repo/app.py", line 2, in greet\nAssertionError: wrong value'  # 构造标准 Python traceback 格式。
                return CommandResult(command, 1, output, "", 0.01)  # 将实际可见输出作为 Runtime 事实。
            return await super().execute(command, timeout_seconds=timeout_seconds)  # Git 状态和索引仍经过真实本地运行时。

    runtime = TracebackRuntime(fixture.repository, fixture.isolation_root)  # 限定测试只访问临时隔离根。
    asyncio.run(PatchFlowAgent(FakeModel((_understanding(),)), config=config.agent).run(task, context, runtime))  # 在计划阶段脚本耗尽前完成修复前复现。
    graph = json.loads((context.layout.run_dir / "evidence_graph.json").read_text(encoding="utf-8"))  # 读取实际落盘证据图。
    frames = [node for node in graph["nodes"] if node["kind"] == "stack_frame"]  # 提取栈帧节点。
    assert len(frames) == 1 and frames[0]["metadata"]["line"] == 2  # 精确保存一开始计数的错误行。
    assert frames[0]["origin"] == "runtime" and frames[0]["source_ref"].startswith("event:")  # 栈帧有正式执行事件来源。
    assert any(edge["relation"] == "fails_at" and edge["target_id"] == frames[0]["node_id"] for edge in graph["edges"])  # 失败节点明确指向栈帧。
