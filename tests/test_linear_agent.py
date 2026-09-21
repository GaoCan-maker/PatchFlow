"""FakeModel 驱动的线性 Agent 端到端测试。"""  # 说明测试覆盖真实仓库和可回放轨迹。

from __future__ import annotations  # 延迟解析类型标注。

import asyncio  # 在同步 pytest 中运行异步 Agent。
import os  # 读取真实 Docker 测试开关。
import sys  # 获取本地环境 Python 解释器。
from itertools import pairwise  # 按相邻事件验证因果关联。
from pathlib import Path  # 标注 pytest 临时目录。

import pytest  # 参数化 Docker 与本地后端。

from patchflow.agent.linear_react import LinearReactAgent  # 导入待测线性策略。
from patchflow.application.run_initializer import (  # 导入正式运行上下文与初始化入口。
    RunContext,  # 标注运行上下文返回类型。
    initialize_run,  # 创建正式运行目录。
)  # 结束运行上下文导入。
from patchflow.config.models import AppConfig, ModelConfig  # 构造不包含 API 密钥的配置。
from patchflow.domain.enums import AgentPhase, EventType, RunStatus  # 检查状态机和事件类别。
from patchflow.domain.task import Budget  # 构造边界预算。
from patchflow.domain.tools import ToolCall  # 构造严格工具调用。
from patchflow.model.fake import FakeModel  # 使用无网络确定性模型。
from patchflow.model.protocol import ModelResponse, ModelUsage  # 构造模型脚本回复。
from patchflow.runtime.docker import DockerRuntime  # 验证真实容器后端。
from patchflow.runtime.local import LocalRuntime  # 验证本地可信仓库后端。
from patchflow.storage.manifest_store import ManifestStore  # 从磁盘读取最终清单。
from patchflow.tools import (  # 注入真实结构化工具。
    ApplyPatchTool,  # 应用候选补丁。
    ReadCodeTool,  # 读取目标代码。
    RunCommandTool,  # 在取消测试中执行受控长命令。
    RunTestsTool,  # 执行公开测试。
    SearchTextTool,  # 搜索仓库文本。
)  # 结束测试工具导入。
from tests.runtime_helpers import (  # 复用临时仓库和补丁。
    create_temporary_git_repository,  # 创建干净仓库。
    valid_patch,  # 生成可应用补丁。
)  # 结束夹具导入。


def _call(call_id: str, name: str, arguments: dict[str, object]) -> ModelResponse:  # 构造单步脚本动作。
    return ModelResponse(tool_call=ToolCall.model_validate({"call_id": call_id, "tool_name": name, "arguments": arguments}), usage=ModelUsage(2, 1))  # 让预算统计可被断言。


def _context(tmp_path: Path, task) -> RunContext:  # 创建单个测试独享的运行目录。
    config = AppConfig(model=ModelConfig(provider="fake", model="scripted"))  # 声明没有远程密钥的假模型。
    return initialize_run(task, config, runs_root=tmp_path / "runs", code_version="test")  # 返回正式运行上下文。


def _agent(model: FakeModel) -> LinearReactAgent:  # 装配明确允许的四个基础工具。
    return LinearReactAgent(model, (SearchTextTool(), ReadCodeTool(), ApplyPatchTool(), RunTestsTool()))  # 不启用高风险一般命令。


@pytest.mark.parametrize("backend", ("local", "docker"))  # 同一 Agent 轨迹在两种隔离后端上验收。
def test_agent_saves_verified_patch_and_trajectory(tmp_path: Path, backend: str) -> None:  # 验证完整搜索到最终补丁的闭环。
    if backend == "docker" and os.environ.get("PATCHFLOW_RUN_DOCKER_TESTS") != "1":  # 默认不依赖 Docker daemon。
        pytest.skip("需要显式开启 Docker 集成测试")  # 保持普通单测可离线运行。
    repository = create_temporary_git_repository(tmp_path)  # 创建干净的独立测试仓库。
    context = _context(tmp_path, repository.task)  # 初始化 run ID、manifest 和首个事件。
    python = "python" if backend == "docker" else sys.executable  # 选择隔离环境内的解释器。
    model = FakeModel((  # 按固定顺序脚本化五次模型决策。
        _call("search", "search_text", {"query": "def greet"}),  # 先定位目标函数。
        _call("read", "read_code", {"path": "app.py", "end_line": 2}),  # 再读取相关代码。
        _call("patch", "apply_patch", {"patch": valid_patch()}),  # 应用真实统一 diff。
        _call("tests", "run_tests", {"command": [python, "-m", "pytest", "-q"], "timeout_seconds": 30}),  # 测试最新修改。
        ModelResponse(final_answer="修复完成且公开测试通过。", usage=ModelUsage(2, 1)),  # 最后请求结束。
    ))  # 完成脚本序列。
    runtime = DockerRuntime(repository.repository) if backend == "docker" else LocalRuntime(repository.repository, repository.isolation_root)  # 选择真实后端。
    outcome = asyncio.run(_agent(model).run(repository.task, context, runtime))  # 执行完整 Agent loop。
    assert outcome.status is RunStatus.SUCCEEDED  # 只有测试通过且存在补丁才能成功。
    assert outcome.stop_reason == "verified_patch"  # 检查稳定停止原因。
    assert "cleaned_name" in context.layout.final_patch_path.read_text(encoding="utf-8")  # 确认磁盘保存完整补丁。
    assert ManifestStore(context.layout.manifest_path).load().status is RunStatus.SUCCEEDED  # 检查清单与结果一致。
    assert context.state.phase is AgentPhase.COMPLETED  # 检查基线阶段进入合法终态。
    assert context.state.usage.model_calls == 5  # 检查模型调用计数。
    assert context.state.usage.tool_calls == 4  # 检查工具调用计数。
    assert context.state.usage.input_tokens == 10  # 检查 provider 报告的输入 token 累计。
    events = context.event_store.load_all()  # 从磁盘回放全部事件。
    assert events[0].event_type is EventType.RUN_CREATED  # 初始化事件仍位于轨迹首位。
    assert sum(event.event_type is EventType.TOOL_COMPLETED for event in events) == 4  # 每个工具都有完成事件。
    assert events[-1].event_type is EventType.RUN_COMPLETED  # 成功事件位于轨迹末尾。
    assert all(current.causation_event_id == previous.event_id for previous, current in pairwise(events))  # 验证事件因果链没有断裂。
    assert "cleaned_name" in model.requests[-1].history[-1].content or "1 passed" in model.requests[-1].history[-1].content  # 确认模型看到了测试反馈。


def test_agent_rejects_failed_test_as_unverified(tmp_path: Path) -> None:  # 验证模型不能凭自述宣布修复成功。
    repository = create_temporary_git_repository(tmp_path)  # 创建干净本地仓库。
    context = _context(tmp_path, repository.task)  # 创建正式运行目录。
    model = FakeModel((  # 脚本故意执行确定失败的测试命令。
        _call("patch", "apply_patch", {"patch": valid_patch()}),  # 先应用可用补丁。
        _call("failed-tests", "run_tests", {"command": [sys.executable, "-c", "raise SystemExit(7)"]}),  # 再让测试失败。
        ModelResponse(final_answer="我认为已经修好。"),  # 模型无视失败结果请求结束。
    ))  # 完成失败轨迹脚本。
    outcome = asyncio.run(_agent(model).run(repository.task, context, LocalRuntime(repository.repository, repository.isolation_root)))  # 运行真实工具。
    assert outcome.status is RunStatus.FAILED  # 不接受失败测试后的自信结论。
    assert outcome.stop_reason == "unverified_candidate"  # 保留具体拒绝原因。
    assert context.layout.final_patch_path.exists() is False  # 不保存未经验证的最终补丁。
    assert "nonzero_exit" in model.requests[-1].history[-1].content  # 确认模型收到真实失败反馈。


def test_unknown_tool_returns_observation_then_budget_stops(tmp_path: Path) -> None:  # 验证无效调用有反馈且计入预算。
    repository = create_temporary_git_repository(tmp_path)  # 创建隔离测试仓库。
    task = repository.task.model_copy(update={"budget": Budget(max_tool_calls=1)})  # 只允许一次工具尝试。
    context = _context(tmp_path, task)  # 按限制预算创建运行。
    model = FakeModel((  # 准备未知工具和第二次越界动作。
        _call("unknown", "delete_everything", {}),  # 未注册工具不得访问运行时。
        _call("second", "search_text", {"query": "greet"}),  # 这次调用应被预算拒绝。
    ))  # 完成预算测试脚本。
    outcome = asyncio.run(_agent(model).run(task, context, LocalRuntime(repository.repository, repository.isolation_root)))  # 运行 Agent。
    assert outcome.status is RunStatus.FAILED  # 预算耗尽是正常任务失败。
    assert outcome.stop_reason == "tool_calls"  # 确认工具硬上限生效。
    assert "unknown_tool" in model.requests[1].history[-1].content  # 确认未知工具反馈进入模型历史。
    assert context.state.usage.tool_calls == 1  # 第二次越界调用没有执行。


def test_model_timeout_records_terminal_failure(tmp_path: Path) -> None:  # 验证全局时间限制覆盖模型等待。
    repository = create_temporary_git_repository(tmp_path)  # 创建独立可信仓库。
    task = repository.task.model_copy(update={"budget": Budget(max_wall_clock_seconds=0.05)})  # 设置很短的墙钟预算。
    context = _context(tmp_path, task)  # 创建正式运行上下文。

    class SlowModel:  # 定义只用于超时测试的异步模型。
        async def complete(self, request) -> ModelResponse:  # 实现相同模型协议。
            await asyncio.sleep(1)  # 故意等待超过任务时限。
            return ModelResponse(final_answer="不会到达此处")  # 提供类型完整的不可达回复。

    outcome = asyncio.run(LinearReactAgent(SlowModel(), ()).run(task, context, LocalRuntime(repository.repository, repository.isolation_root)))  # 运行全局限时 Agent。
    assert outcome.stop_reason == "wall_clock_seconds"  # 确认时间预算触发。
    assert outcome.status is RunStatus.FAILED  # 超时不会报告成功。
    assert context.event_store.load_all()[-1].event_type is EventType.RUN_FAILED  # 确认终态可回放。


def test_test_feedback_is_invalidated_by_later_write(tmp_path: Path) -> None:  # 防止使用修复前测试冒充最终验证。
    repository = create_temporary_git_repository(tmp_path)  # 创建有通过公开测试的基础仓库。
    context = _context(tmp_path, repository.task)  # 初始化审计目录。
    model = FakeModel((  # 构造先通过测试再修改的错误顺序。
        _call("early-test", "run_tests", {"command": [sys.executable, "-m", "pytest", "-q"]}),  # 基础代码测试通过。
        _call("late-patch", "apply_patch", {"patch": valid_patch()}),  # 测试后改变代码。
        ModelResponse(final_answer="现在结束。"),  # 故意跳过修改后的重测。
    ))  # 完成脚本序列。
    outcome = asyncio.run(_agent(model).run(repository.task, context, LocalRuntime(repository.repository, repository.isolation_root)))  # 执行错误顺序。
    assert outcome.stop_reason == "unverified_candidate"  # 旧测试结果必须失效。
    assert context.layout.final_patch_path.exists() is False  # 未验证补丁不得导出。


def test_private_evaluation_reference_is_not_sent_to_model(tmp_path: Path) -> None:  # 验证评测私有元数据不进入模型请求。
    repository = create_temporary_git_repository(tmp_path)  # 创建临时任务仓库。
    task = repository.task.model_copy(update={"evaluation_ref": "private-hidden-tests"})  # 注入仅评测层可见字段。
    context = _context(tmp_path, task)  # 初始化任务运行目录。
    model = FakeModel((ModelResponse(final_answer="无法完成。"),))  # 让模型立即结束。
    asyncio.run(_agent(model).run(task, context, LocalRuntime(repository.repository, repository.isolation_root)))  # 运行一次模型决策。
    assert model.requests[0].problem_statement == task.problem_statement  # 公开 Issue 必须可见。
    assert not hasattr(model.requests[0], "evaluation_ref")  # 私有评测引用不得暴露。
    assert "private-hidden-tests" not in repr(model.requests[0])  # 防止通过请求字符串间接泄漏。


def test_token_budget_blocks_tool_execution(tmp_path: Path) -> None:  # 验证超量模型回复不会继续调用工具。
    repository = create_temporary_git_repository(tmp_path)  # 创建干净仓库。
    task = repository.task.model_copy(update={"budget": Budget(max_input_tokens=1)})  # 限制输入 token 为一。
    context = _context(tmp_path, task)  # 初始化独立运行目录。
    model = FakeModel((_call("never-run", "search_text", {"query": "greet"}),))  # 预设两 token 的超量动作。
    outcome = asyncio.run(_agent(model).run(task, context, LocalRuntime(repository.repository, repository.isolation_root)))  # 执行预算测试。
    assert outcome.stop_reason == "token_budget"  # 返回明确的 token 超限原因。
    assert context.state.usage.tool_calls == 0  # 搜索工具不得执行。
    assert sum(event.event_type is EventType.TOOL_REQUESTED for event in context.event_store.load_all()) == 0  # 轨迹不得伪造工具请求。


def test_wall_clock_timeout_cancels_local_command(tmp_path: Path) -> None:  # 验证全局取消可以结束正在运行的本地命令。
    repository = create_temporary_git_repository(tmp_path)  # 创建独立可信测试仓库。
    task = repository.task.model_copy(update={"budget": Budget(max_wall_clock_seconds=0.4)})  # 为整个任务设置短时限。
    context = _context(tmp_path, task)  # 创建正式运行上下文。
    model = FakeModel((_call("slow-command", "run_command", {"command": [sys.executable, "-c", "import time; time.sleep(5)  # 模拟长命令"], "timeout_seconds": 10}),))  # 让单命令超时长于总预算。
    runtime = LocalRuntime(repository.repository, repository.isolation_root)  # 使用有进程组清理能力的本地 Runtime。
    outcome = asyncio.run(LinearReactAgent(model, (RunCommandTool(),)).run(task, context, runtime))  # 执行并触发全局取消。
    assert outcome.stop_reason == "wall_clock_seconds"  # 确认总预算先于命令超时生效。
    assert outcome.status is RunStatus.FAILED  # 长命令不能产生成功结果。
    assert context.layout.final_patch_path.exists() is False  # 取消后不导出补丁。
