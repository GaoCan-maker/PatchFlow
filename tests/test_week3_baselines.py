"""第三周三策略和上下文边界的确定性测试。"""  # FakeModel 不发起付费 API 请求。

from __future__ import annotations  # 延迟解析类型标注。

import asyncio  # 在同步 pytest 中运行异步基线。
import difflib  # 从固定源码构造标准统一补丁。
import json  # 检查持久化批量报告。
import os  # 读取真实 Docker 测试开关。
import subprocess  # 在独立烟测仓库复现修复前公开测试失败。
import sys  # 使用当前 pytest 环境的 Python 解释器。
from pathlib import Path  # 接收隔离的 pytest 临时目录。

import pytest  # 使用异常断言与可选 Docker 跳过。

from patchflow.agent.bash_only import BashOnlyAgent  # 测试容器强制要求。
from patchflow.agent.context import ContextBuilder, ContextTooLargeError  # 测试有界历史。
from patchflow.cli import main  # 测试默认不收费的命令行入口。
from patchflow.config.models import AppConfig, ModelConfig, RuntimeConfig  # 构造无密钥实验配置。
from patchflow.domain.tools import ToolCall  # 构造原生函数调用历史。
from patchflow.evaluation.batch import BatchRunner  # 驱动统一批量报告。
from patchflow.evaluation.smoke import prepare_smoke_dataset  # 准备两个修复前失败的任务。
from patchflow.model.fake import FakeModel  # 使用固定决策脚本替代远程模型。
from patchflow.model.protocol import (  # 构造消息与可计数回复。
    ModelMessage,  # 保存单条模型可见历史。
    ModelResponse,  # 保存脚本式模型决策。
    ModelUsage,  # 保存模型调用用量。
)  # 结束模型协议导入。
from patchflow.runtime.local import LocalRuntime  # 仅用于验证 Bash-only 拒绝宿主执行。
from tests.runtime_helpers import create_temporary_git_repository  # 创建可信本地拒绝用例。


def _call(call_id: str, name: str, arguments: dict[str, object]) -> ModelResponse:  # 构造带用量的模型工具动作。
    return ModelResponse(tool_call=ToolCall.model_validate({"call_id": call_id, "tool_name": name, "arguments": arguments}), usage=ModelUsage(3, 2))  # 保留调用 ID 供历史配对。


def _patch(task_id: str) -> tuple[str, str]:  # 返回指定烟测任务的 Git 补丁与 Bash 修改脚本。
    if task_id == "micro-greet-strip":  # 选择姓名规范化任务。
        old = "def greet(name: str) -> str:  # 生成问候语。\n    return f'Hello, {name}!'  # 当前未规范化输入。\n"  # 保持与数据集初始文件完全一致。
        new = "def greet(name: str) -> str:  # 生成问候语。\n    return f'Hello, {name.strip()}!'  # 去除输入两端空白。\n"  # 写入预期修复。
    else:  # 处理列表空项过滤任务。
        old = "def parse_items(raw: str) -> list[str]:  # 解析逗号分隔输入。\n    return [part.strip() for part in raw.split(',')]  # 当前保留空白项。\n"  # 保持初始文件原文。
        new = "def parse_items(raw: str) -> list[str]:  # 解析逗号分隔输入。\n    return [part.strip() for part in raw.split(',') if part.strip()]  # 忽略空白项。\n"  # 写入预期过滤逻辑。
    patch = "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile="a/app.py", tofile="b/app.py"))  # 构造标准 Git 可应用补丁。
    script = "from pathlib import Path; p=Path('app.py'); p.write_text(" + repr(new) + ", encoding='utf-8')"  # 仅在隔离测试容器中完整写入目标源码。
    return patch, script  # 返回与同一个目标修复对应的两种动作。


def test_context_preserves_latest_complete_tool_pair() -> None:  # 确保截断不会产生孤立工具消息。
    first = ToolCall(call_id="first", tool_name="search_text", arguments={"query": "old"})  # 构造较旧动作。
    second = ToolCall(call_id="second", tool_name="run_tests", arguments={"command": ["pytest"]})  # 构造最新测试动作。
    history = (ModelMessage("system", "修复 bug"), ModelMessage("assistant", "", tool_call=first), ModelMessage("tool", json.dumps({"success": False, "error_type": "not_found", "stdout": "x" * 2500}), tool_call_id="first"), ModelMessage("assistant", "", tool_call=second), ModelMessage("tool", json.dumps({"success": False, "error_type": "test_failed", "stdout": "y" * 200}), tool_call_id="second"))  # 混合长旧反馈与短新反馈。
    selected = ContextBuilder(max_bytes=1800).build(history, ())  # 施加小于完整历史的请求预算。
    assert selected.dropped_messages == 2  # 旧工具调用和旧结果被成对省略。
    assert selected.messages[-2].tool_call == second  # 最新完整调用仍可见。
    assert selected.messages[-1].tool_call_id == "second"  # 最新测试反馈仍与调用配对。
    assert selected.estimated_bytes <= 1800  # 最终输入满足硬字节界限。
    with pytest.raises(ContextTooLargeError):  # 固定 Issue 超限时不允许静默截断。
        ContextBuilder(max_bytes=512).build((ModelMessage("system", "z" * 1000),), ())  # 保证异常语义稳定。


def test_bash_only_rejects_local_runtime(tmp_path: Path) -> None:  # 未知命令不能在宿主机执行。
    repository = create_temporary_git_repository(tmp_path)  # 创建可信本地仓库仅用于拒绝验证。
    from patchflow.application.run_initializer import initialize_run  # 导入标准上下文初始化器。
    context = initialize_run(repository.task, AppConfig(), runs_root=tmp_path / "runs")  # 初始化未被消费的运行上下文。
    with pytest.raises(ValueError, match="DockerRuntime"):  # 检查 Agent 在启动前拒绝 LocalRuntime。
        asyncio.run(BashOnlyAgent(FakeModel(())).run(repository.task, context, LocalRuntime(repository.repository, repository.isolation_root)))  # 不应执行任意模型命令。


def test_smoke_cli_is_offline_and_spending_requires_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # 验证批量入口的付费保护。
    root = tmp_path / "smoke"  # 选择当前测试独占的数据集目录。
    assert main(["prepare-smoke", str(root)]) == 0  # 离线创建固定两任务数据集。
    assert main(["validate", str(root / "tasks.json")]) == 0  # 不用密钥也能校验任务。
    with pytest.raises(SystemExit) as error:  # 真实模型路径未确认付费时必须退出。
        main(["run-batch", str(root / "tasks.json"), "--provider", "openai_chat", "--model-id", "example", "--runs-root", str(tmp_path / "runs"), "--report", str(tmp_path / "report.json")])  # 故意不提供付费开关。
    assert error.value.code == 2  # 检查标准 argparse 拒绝退出码。
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # 确保即使宿主已有密钥也检查缺失配置路径。
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):  # 明确付费但未配置密钥时应在创建 run 前失败。
        main(["run-batch", str(root / "tasks.json"), "--provider", "openai_chat", "--model-id", "example", "--runs-root", str(tmp_path / "runs"), "--report", str(tmp_path / "report.json"), "--allow-api-spend"])  # 预检不得发送网络请求。
    assert not (tmp_path / "runs").exists()  # 失败的预检不得留下空运行记录。


def test_smoke_tasks_fail_before_any_patch(tmp_path: Path) -> None:  # 验证数据集确实有回归信号。
    tasks = prepare_smoke_dataset(tmp_path / "dataset")  # 创建独立基础提交任务。
    for task in tasks:  # 两个任务都应先失败再谈修复。
        result = subprocess.run((sys.executable, "-m", "pytest", "-q"), cwd=task.repo_spec.location, capture_output=True, text=True, check=False)  # 在只包含固定测试的临时仓库运行公开命令。
        assert result.returncode != 0  # 修复前不得意外全绿。
        assert "1 failed" in result.stdout  # 确认失败来自已知的目标回归测试。


def test_independent_evaluator_rejects_applicable_but_wrong_patch(tmp_path: Path) -> None:  # 验证评测不相信 Agent 自称成功。
    if os.environ.get("PATCHFLOW_RUN_DOCKER_TESTS") != "1":  # 仅在显式启用时连接 Docker daemon。
        pytest.skip("需要显式开启 Docker 集成测试")  # 普通单测保持无 Docker 依赖。
    task = prepare_smoke_dataset(tmp_path / "dataset")[0]  # 选择姓名规范化任务。
    wrong_patch = "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n def greet(name: str) -> str:  # 生成问候语。\n+    name = name  # 无效修改不会解决空白缺陷。\n     return f'Hello, {name}!'  # 当前未规范化输入。\n"  # 构造可应用但不修复行为的补丁。
    config = AppConfig(model=ModelConfig(provider="fake", model="scripted"), runtime=RuntimeConfig(kind="docker"))  # 使用与主评测相同的 Docker 配置。
    evaluator = BatchRunner(config, lambda strategy, current: FakeModel(()), runs_root=tmp_path / "runs")  # 模型工厂不会在独立评测中被调用。
    applied, passed, reason = asyncio.run(evaluator._evaluate(task, wrong_patch))  # 重新应用补丁并执行公开测试。
    assert applied and not passed and reason == "public_test_failed"  # 区分语法可用与行为修复成功。


def test_three_baselines_share_two_smoke_tasks_and_independent_evaluation(tmp_path: Path) -> None:  # 真实 Docker 批量验收。
    if os.environ.get("PATCHFLOW_RUN_DOCKER_TESTS") != "1":  # 普通快速测试默认不依赖 daemon。
        pytest.skip("需要显式开启 Docker 集成测试")  # 保留可选重型测试。
    tasks = prepare_smoke_dataset(tmp_path / "dataset")  # 创建两个修复前失败的 MicroSWE 任务。
    models: dict[tuple[str, str], FakeModel] = {}  # 保存模型以检查 one-shot 可见历史。
    def factory(strategy: str, task) -> FakeModel:  # 为每个任务策略组合提供全新脚本模型。
        patch, script = _patch(task.task_id)  # 取得该任务的预期修复动作。
        if strategy == "one_shot":  # 单轮策略直接输出补丁。
            responses = (ModelResponse(final_answer=patch, usage=ModelUsage(3, 2)),)  # 仅提供一次模型回复。
        elif strategy == "linear_react":  # 线性策略经过补丁和公开测试反馈。
            responses = (_call("patch", "apply_patch", {"patch": patch}), _call("tests", "run_tests", {"command": ["python", "-m", "pytest", "-q"]}), ModelResponse(final_answer="测试已通过。"))  # 保留完整反馈循环。
        else:  # Bash-only 策略仅通过通用命令修改和测试。
            responses = (_call("edit", "run_command", {"command": ["python", "-c", script]}), _call("tests", "run_command", {"command": ["python", "-m", "pytest", "-q"]}), ModelResponse(final_answer="测试已通过。"))  # 精确匹配公开测试 argv。
        model = FakeModel(responses)  # 创建无网络模型替身。
        models[(strategy, task.task_id)] = model  # 记录以供后续断言。
        return model  # 返回当前组合专用模型。
    config = AppConfig(model=ModelConfig(provider="fake", model="scripted"), runtime=RuntimeConfig(kind="docker"))  # 声明统一实验配置。
    report_path = tmp_path / "report.json"  # 选择机器可读报告输出位置。
    report = asyncio.run(BatchRunner(config, factory, runs_root=tmp_path / "runs").run(tasks, report_path=report_path))  # 在独立容器上完成六次生成与六次评测。
    assert report.dataset_size == 2  # 两个任务进入相同对照分母。
    assert len(report.cases) == 6  # 三策略乘两任务产生六个独立结果。
    assert all(case.patch_applied and case.public_passed for case in report.cases)  # 每个最终补丁都能在全新容器公开测试通过。
    assert all(report.summary[name]["public_pass_rate"] == 1.0 for name in ("one_shot", "linear_react", "bash_only"))  # 只验证确定性假模型烟测。
    assert all(len(models[("one_shot", task.task_id)].requests) == 1 for task in tasks)  # 单轮基线只询问一次模型。
    assert all(not models[("one_shot", task.task_id)].requests[0].tools for task in tasks)  # 单轮基线没有工具定义。
    assert json.loads(report_path.read_text(encoding="utf-8"))["dataset_size"] == 2  # 报告文件与内存结果一致。
